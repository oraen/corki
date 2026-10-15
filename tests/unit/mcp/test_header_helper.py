import asyncio
import gc
import json
import os
import shlex
import sys
import weakref

import httpx
import pytest

from corki.config import MCPServerSettings
from corki.mcp.auth_challenge import insufficient_scope
from corki.mcp.header_helper import HeaderHelper
from corki.mcp.header_helper_process import RESERVED, parse_output, run_helper
from corki.mcp.reconciliation import connection_identity


@pytest.mark.parametrize("name", sorted(RESERVED))
def test_helper_reserved_names_are_rejected_without_echoing_output(name):
    with pytest.raises(ValueError) as caught:
        parse_output(json.dumps({name.upper(): "fixture-secret"}).encode())
    assert "fixture-secret" not in str(caught.value)


@pytest.mark.parametrize(
    "output",
    [
        b'{"Secret":"x","secret":"y"}',
        b'{"secret":"x","secret":"y"}',
        b'{"bad name":"secret"}',
        b'{"X":"secret\\n"}',
        b'{"X":1}',
        b'{"X":{}}',
        b"[]",
        b"{} {}",
        b"\xff",
    ],
)
def test_bad_output_is_strict_and_secret_safe(output):
    with pytest.raises(ValueError) as caught:
        parse_output(output)
    assert "secret" not in str(caught.value).lower()


@pytest.mark.parametrize(
    "header,expected",
    [
        ('Bearer error="insufficient_scope", scope="tools:write"', True),
        ('Basic realm="x", Bearer error=insufficient_scope', True),
        ('bEaReR ERROR="insufficient\\_scope"', True),
        ("Bearer error=insufficient_scope; scope=read", True),
        ("Bearer error=insufficient_scope, error=insufficient_scope", False),
        ("Basic error=insufficient_scope", False),
        ('Bearer error="insufficient_scope', False),
        ("Bearer error=invalid_token", False),
        ("Bearer error=insufficient_scope, Basic realm=x", True),
    ],
)
def test_source_auth_challenge_recognition(header, expected):
    assert insufficient_scope(header) is expected


@pytest.mark.parametrize("value", ["", " \t", True, [], 1])
def test_helper_config_rejects_invalid_commands(value):
    with pytest.raises(ValueError, match="http_headers_helper"):
        MCPServerSettings.from_mapping(
            "docs", {"url": "https://fixture.test", "http_headers_helper": value}
        )


@pytest.mark.parametrize("environment", ["remote", "", False, 1])
def test_nonlocal_helper_declaration_never_becomes_implicit_local_execution(environment):
    # Invalid field types fail before transport-specific helper validation.
    error = (
        "local environment" if isinstance(environment, str) else "environment_id must be a string"
    )
    with pytest.raises(ValueError, match=error):
        MCPServerSettings.from_mapping(
            "docs",
            {
                "url": "https://fixture.test",
                "http_headers_helper": "must-not-run",
                "environment_id": environment,
            },
        )


def test_helper_identity_tracks_command_and_cwd_but_no_unreferenced_ambient(tmp_path, monkeypatch):
    from dataclasses import replace

    settings = MCPServerSettings(
        "docs", "http", url="https://fixture.test", http_headers_helper="fixture", cwd=tmp_path
    )
    identity = connection_identity(settings)
    monkeypatch.setenv("CORKI_HELPER_UNRELATED", "value")
    assert connection_identity(settings) == identity
    assert connection_identity(replace(settings, http_headers_helper="changed")) != identity
    assert connection_identity(replace(settings, cwd=tmp_path / "other")) != identity
    assert (
        MCPServerSettings("docs", "http", http_headers_helper="\x1c").http_headers_helper == "\x1c"
    )
    with pytest.raises(ValueError, match="only supported for http"):
        MCPServerSettings.from_mapping(
            "docs", {"command": "fixture", "http_headers_helper": "helper"}
        )


@pytest.mark.skipif(os.name != "posix", reason="owned POSIX helper")
@pytest.mark.parametrize("outcome", ["ok", "nonzero", "oversized", "timeout"])
def test_actual_helper_environment_output_exit_timeout_and_cleanup(tmp_path, monkeypatch, outcome):
    async def scenario():
        import corki.mcp.header_helper_process as module

        pid = tmp_path / "pid"
        monkeypatch.setenv("CORKI_HELPER_UNREQUESTED", "must-not-inherit")
        code = (
            "import os,json,time; from pathlib import Path; "
            f"Path({str(pid)!r}).write_text(str(os.getpid())); "
            "assert 'CORKI_HELPER_UNREQUESTED' not in os.environ; assert 'PATH' in os.environ; "
        )
        code += {
            "ok": "print(json.dumps({'X-Test':'café'}))",
            "nonzero": "print('{}'); raise SystemExit(23)",
            "oversized": "print('x'*65537)",
            "timeout": "time.sleep(30)",
        }[outcome]
        if outcome == "timeout":
            monkeypatch.setattr(module, "HELPER_TIMEOUT", 0.05)
        command = shlex.join([sys.executable, "-c", code])
        if outcome == "ok":
            assert (await run_helper(command, tmp_path))["x-test"] == "café"
        else:
            with pytest.raises(
                ValueError,
                match={"nonzero": "status 23", "oversized": "64 KiB", "timeout": "timed out"}[
                    outcome
                ],
            ):
                await run_helper(command, tmp_path)
        if pid.exists():
            with pytest.raises(ProcessLookupError):
                os.kill(int(pid.read_text()), 0)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status,key", [(401, "authorization"), (401, "proxy-authorization"), (403, "x-key")]
)
def test_concurrent_rejected_posts_share_one_refresh_and_restore_configured_fallback(
    tmp_path, monkeypatch, status, key
):
    async def scenario():
        invocations, sends = [], []
        barrier = asyncio.Event()

        async def run(*args):
            invocations.append(1)
            return httpx.Headers(
                {key: "old", "x-fallback": "helper-old"} if len(invocations) == 1 else {key: "new"}
            )

        monkeypatch.setattr("corki.mcp.header_helper.run_helper", run)
        helper = HeaderHelper("https://fixture.test", "fixture", tmp_path)

        async def send(headers, timeout):
            sends.append(dict(headers))
            assert headers["mcp-session-id"] == "session"
            if headers[key] == "old":
                if len(sends) == 2:
                    barrier.set()
                await barrier.wait()
                return httpx.Response(status, content=b"rejected")
            assert headers["x-fallback"] == "configured"
            return httpx.Response(204)

        original = httpx.Headers({"x-fallback": "configured", "mcp-session-id": "session"})
        try:
            results = await asyncio.gather(
                *(
                    helper.request("POST", "https://fixture.test", original, 2, send)
                    for _ in range(2)
                )
            )
            assert [r.status_code for r in results] == [204, 204]
            assert len(invocations) == 2 and len(sends) == 4
        finally:
            await helper.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "case", ["unchanged", "failed", "masked", "scope", "delete", "cross", "network", "twice"]
)
def test_refresh_never_becomes_unbounded_or_unknown_outcome_retry(tmp_path, monkeypatch, case):
    async def scenario():
        invocations, sends = [], []

        async def run(*args):
            invocations.append(1)
            if case == "failed" and len(invocations) > 1:
                raise ValueError("helper failed")
            value = "old" if len(invocations) == 1 or case == "unchanged" else "new"
            return httpx.Headers({"authorization" if case == "masked" else "x-key": value})

        monkeypatch.setattr("corki.mcp.header_helper.run_helper", run)
        helper = HeaderHelper("https://fixture.test", "fixture", tmp_path)
        response = httpx.Response(
            403 if case == "scope" else 401,
            headers={
                "www-authenticate": 'Bearer error="insufficient_scope"'
                if case == "scope"
                else 'Bearer realm="fixture"'
            },
            content=b"original challenge",
        )

        async def send(headers, timeout):
            sends.append(dict(headers))
            if case == "network":
                raise httpx.ReadError("unknown result")
            return response

        try:
            call = helper.request(
                "DELETE" if case == "delete" else "POST",
                "https://other.test" if case == "cross" else "https://fixture.test",
                httpx.Headers({"Authorization": "explicit"})
                if case == "masked"
                else httpx.Headers(),
                2,
                send,
            )
            if case == "network":
                with pytest.raises(httpx.ReadError):
                    await call
            else:
                assert await call is response
            assert len(sends) == (2 if case == "twice" else 1)
            assert len(invocations) == (
                0 if case == "cross" else 1 if case in {"scope", "delete", "network"} else 2
            )
            assert response.content == b"original challenge"
        finally:
            await helper.aclose()

    asyncio.run(scenario())


def test_refresh_rechecks_proxy_redirect_and_uses_remaining_deadline(tmp_path, monkeypatch):
    async def scenario():
        runs, sent = [], []

        async def run(*args):
            runs.append(1)
            return httpx.Headers({} if len(runs) == 1 else {"proxy-authorization": "new"})

        monkeypatch.setattr("corki.mcp.header_helper.run_helper", run)
        helper = HeaderHelper("http://fixture.test", "fixture", tmp_path)

        async def send(headers, seconds):
            sent.append((dict(headers), seconds))
            await asyncio.sleep(0.01)
            return httpx.Response(
                401 if len(sent) == 1 else 307, headers={"location": "http://other.test"}
            )

        try:
            with pytest.raises(ValueError, match="cannot safely replay"):
                await helper.request("POST", "http://fixture.test", httpx.Headers(), 1, send)
            assert len(sent) == 2 and sent[1][1] < sent[0][1]
        finally:
            await helper.aclose()

    asyncio.run(scenario())


@pytest.mark.skipif(os.name != "posix", reason="real owned helper subprocess")
def test_cancelled_manager_initialization_reclaims_helper_process(tmp_path, monkeypatch):
    async def scenario():
        from corki.mcp.client import HttpMCPClient
        from corki.mcp.manager import MCPManager
        from corki.tools import ToolRegistry

        pid = tmp_path / "pid"
        script = (
            "import os,time; from pathlib import Path; "
            f"Path({str(pid)!r}).write_text(str(os.getpid())); time.sleep(30)"
        )
        command = shlex.join([sys.executable, "-c", script])
        clients = []

        def factory(settings):
            def forbidden(request):
                pytest.fail("HTTP request sent before helper completed")

            client = HttpMCPClient(settings, transport=httpx.MockTransport(forbidden))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        manager = MCPManager(
            (
                MCPServerSettings(
                    "docs",
                    "http",
                    url="https://fixture.test",
                    http_headers_helper=command,
                    cwd=tmp_path,
                ),
            ),
            ToolRegistry(),
        )
        startup = asyncio.create_task(manager.start())
        try:
            async with asyncio.timeout(3):
                while not pid.exists() or not pid.read_text():
                    await asyncio.sleep(0.01)
            child = int(pid.read_text())
            startup.cancel()
            with pytest.raises(asyncio.CancelledError):
                await startup
            async with asyncio.timeout(3):
                while True:
                    try:
                        os.kill(child, 0)
                    except ProcessLookupError:
                        break
                    await asyncio.sleep(0.01)
            assert clients[0].is_closed and clients[0]._helper._state.closed
        finally:
            startup.cancel()
            await asyncio.gather(startup, return_exceptions=True)
            await manager.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [False, True])
def test_shared_attempt_survives_waiter_cancel_and_caches_result_or_error(
    tmp_path, monkeypatch, failure
):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def run(*args):
            calls.append(1)
            entered.set()
            await release.wait()
            if failure:
                raise ValueError("fixture failure")
            return httpx.Headers({"x-key": "value"})

        monkeypatch.setattr("corki.mcp.header_helper.run_helper", run)
        helper = HeaderHelper("https://fixture.test", "fixture", tmp_path)
        waiter = asyncio.create_task(helper.headers())
        await entered.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        release.set()
        for _ in range(2):
            if failure:
                with pytest.raises(ValueError, match="fixture failure"):
                    await helper.headers()
            else:
                assert (await helper.headers())[1]["x-key"] == "value"
        assert calls == [1]
        await helper.aclose()

    asyncio.run(scenario())


def test_failed_refresh_advances_cohort_without_replacing_current_credentials(
    tmp_path, monkeypatch
):
    async def scenario():
        calls = []

        async def run(*args):
            calls.append(1)
            if len(calls) > 1:
                raise ValueError("failed refresh")
            return httpx.Headers({"x-key": "old"})

        monkeypatch.setattr("corki.mcp.header_helper.run_helper", run)
        helper = HeaderHelper("https://fixture.test", "fixture", tmp_path)
        epoch, initial = await helper.headers()
        with pytest.raises(ValueError):
            await helper.refresh(epoch)
        assert await helper.refresh(epoch) == initial
        next_epoch, current = await helper.headers()
        assert next_epoch == epoch + 1 and current == initial
        with pytest.raises(ValueError):
            await helper.refresh(next_epoch)
        assert len(calls) == 3
        await helper.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["close", "drop"])
def test_owner_close_or_drop_cancels_and_joins_live_attempt(tmp_path, monkeypatch, action):
    async def scenario():
        entered, cleaned = asyncio.Event(), asyncio.Event()

        async def run(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        monkeypatch.setattr("corki.mcp.header_helper.run_helper", run)
        helper = HeaderHelper("https://fixture.test", "fixture", tmp_path)
        waiter = asyncio.create_task(helper.headers())
        await entered.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        # A finished task may retain exception traceback references until released.
        del waiter
        await asyncio.sleep(0)
        if action == "close":
            await helper.aclose()
        else:
            reference = weakref.ref(helper)
            del helper
            gc.collect()
            assert reference() is None
        await asyncio.wait_for(cleaned.wait(), 2)

    asyncio.run(scenario())


@pytest.mark.skipif(os.name != "posix", reason="actual POSIX spawn handoff")
def test_helper_spawn_handoff_remains_owned_through_repeated_cancel(tmp_path, monkeypatch):
    async def scenario():
        original = asyncio.create_subprocess_exec
        entered, release = asyncio.Event(), asyncio.Event()
        processes = []

        async def spawn(*args, **kwargs):
            process = await original(*args, **kwargs)
            processes.append(process)
            entered.set()
            await release.wait()
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        command = shlex.join([sys.executable, "-c", "import time; time.sleep(30)"])
        call = asyncio.create_task(run_helper(command, tmp_path))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            call.cancel()
            await asyncio.sleep(0)
            call.cancel()
            await asyncio.sleep(0)
            assert not call.done() and processes[0].returncode is None
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(call, 3)
            assert processes[0].returncode is not None
        finally:
            release.set()
            call.cancel()
            await asyncio.gather(call, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.skipif(os.name != "posix", reason="actual POSIX provider Drop")
def test_dropping_helper_owner_reclaims_real_running_process(tmp_path):
    async def scenario():
        pid = tmp_path / "pid"
        script = (
            "import os,time; from pathlib import Path; "
            f"Path({str(pid)!r}).write_text(str(os.getpid())); time.sleep(30)"
        )
        helper = HeaderHelper(
            "https://fixture.test", shlex.join([sys.executable, "-c", script]), tmp_path
        )
        waiter = asyncio.create_task(helper.headers())
        async with asyncio.timeout(3):
            while not pid.exists() or not pid.read_text():
                await asyncio.sleep(0.01)
        child = int(pid.read_text())
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        del waiter
        await asyncio.sleep(0)
        reference = weakref.ref(helper)
        del helper
        gc.collect()
        assert reference() is None
        async with asyncio.timeout(3):
            while True:
                try:
                    os.kill(child, 0)
                except ProcessLookupError:
                    break
                await asyncio.sleep(0.01)
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_cached_headers_are_not_mutable_through_callers(tmp_path, monkeypatch):
    async def scenario():
        async def run(*args):
            return httpx.Headers({"x-key": "original"})

        monkeypatch.setattr("corki.mcp.header_helper.run_helper", run)
        helper = HeaderHelper("https://fixture.test", "fixture", tmp_path)
        try:
            epoch, values = await helper.headers()
            values["x-key"] = "changed"
            assert (await helper.headers())[1]["x-key"] == "original"
            refreshed = await helper.refresh(epoch)
            refreshed["x-key"] = "changed"
            assert (await helper.headers())[1]["x-key"] == "original"
        finally:
            await helper.aclose()

    asyncio.run(scenario())
