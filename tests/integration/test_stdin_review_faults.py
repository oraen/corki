"""Review failures cannot degrade into unreviewed input or lose cancellation owners."""

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest
from test_execution_approval_cancel import observe
from test_execution_policy_live_inheritance import compiler as compiler
from test_execution_policy_live_inheritance import runtime_for
from test_stdin_approval import Model, call, settings_for

from corki.execution.owned_process import run_owned
from corki.protocol.events import TurnCancelled


@asynccontextmanager
async def terminal(root, compiler, mode, monkeypatch):
    model = Model(mode)
    runtime = runtime_for(
        root, compiler, model, settings=settings_for(root, compiler, mode, monkeypatch)
    )
    prompts = []

    async def approve(request):
        prompts.append(request)
        runtime.respond_execution_approval(request.request_id, "accept")

    runtime.set_execution_approval_handler(approve)
    try:
        await call(
            runtime,
            model,
            "exec_command",
            {
                "cmd": "read line; printf '%s' \"$line\" > received",
                "tty": True,
                "login": False,
                "yield_time_ms": 250,
                "sandbox_permissions": "require_escalated",
            },
        )
        session = next(iter(runtime._process_manager._sessions.values()))
        yield runtime, model, session, prompts
    finally:
        await runtime.aclose()


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize(
    "response",
    [
        {"error": 7, "ok": {"revision": 1, "review": False, "reason": None}},
        {"error": None, "ok": {"revision": 1, "review": False, "reason": None}},
        {"ok": {"revision": 1, "review": False, "reason": None, "error": "denied"}},
        None,
        [],
        {"error": "classification failed"},
        {"error": 7},
        {"ok": {"revision": True, "review": False, "reason": None}},
        {"ok": {"revision": 1, "review": 0, "reason": None}},
        {"ok": {"revision": 1, "review": False, "reason": "contradiction"}},
        b'{"ok":{"revision":1,"review":true,"review":false,"reason":null}}',
        b'{"ok":{},"ok":{"revision":1,"review":false,"reason":null}}',
        b"{invalid json",
    ],
)
def test_contradictory_verdict_cannot_skip_approval(
    tmp_path, compiler, mode, response, monkeypatch
):
    async def scenario():
        async with terminal(tmp_path, compiler, mode, monkeypatch) as (
            runtime,
            model,
            session,
            prompts,
        ):
            classified = []

            async def broken(*args, **kwargs):
                classified.append(args)
                return response if isinstance(response, bytes) else json.dumps(response).encode()

            monkeypatch.setattr("corki.execution.terminal.run_owned", broken)
            await call(
                runtime,
                model,
                "write_stdin",
                {"session_id": session.id, "chars": "unsafe\n", "yield_time_ms": 250},
            )
            assert not (tmp_path / "received").exists()
            assert len(classified) == 1
            assert len(prompts) == 1
            assert session.id in runtime._process_manager._sessions
            assert not runtime._process_manager._stdin_reviews
            assert not runtime._process_manager.approvals.router._pending

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("action", ["cancel", "close"])
def test_classification_cancel_joins_actual_helper(tmp_path, compiler, mode, action, monkeypatch):
    async def scenario():
        async with terminal(tmp_path, compiler, mode, monkeypatch) as (
            runtime,
            model,
            session,
            prompts,
        ):
            entered, finalized = asyncio.Event(), asyncio.Event()
            helpers = []
            command = [sys.executable, "-c", "import time; time.sleep(60)"]
            spawn = asyncio.create_subprocess_exec

            async def capture(*args, **kwargs):
                process = await spawn(*args, **kwargs)
                if list(args) == command:
                    helpers.append(process)
                    entered.set()
                return process

            async def classify(*args, **kwargs):
                try:
                    return await run_owned(command, b"", cwd=tmp_path, output_limit=100)
                finally:
                    finalized.set()

            monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
            monkeypatch.setattr("corki.execution.terminal.run_owned", classify)
            model.pending = (
                "write_stdin",
                {"session_id": session.id, "chars": "unsafe\n", "yield_time_ms": 250},
            )
            work = asyncio.create_task(observe(runtime, "cancel native review"))
            try:
                async with asyncio.timeout(8):
                    await entered.wait()
                    assert len(runtime._process_manager._stdin_reviews) == 1
                    if action == "close":
                        await runtime.aclose()
                    else:
                        await runtime.cancel_active()
                    events = await work
                assert isinstance(events[-1], TurnCancelled)
                assert len(helpers) == 1 and helpers[0].returncode is not None
                assert finalized.is_set()
                assert not (tmp_path / "received").exists() and len(prompts) == 1
                assert not runtime._process_manager._stdin_reviews
                assert not session.interaction_lock.locked()
            finally:
                if not work.done():
                    work.cancel()
                await asyncio.gather(work, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("extra", [0, 1])
def test_full_serialized_utf8_boundary(tmp_path, compiler, mode, extra, monkeypatch):
    async def scenario():
        async with terminal(tmp_path, compiler, mode, monkeypatch) as (
            runtime,
            model,
            session,
            prompts,
        ):
            request = {
                "review_terminal": {
                    "launch": session.permissions.wire(),
                    "current": replace(session.permissions, bypassed=False).wire(),
                    "approval_policy": "on-request",
                }
            }
            verdict = json.loads(
                await run_owned(
                    [str(compiler)],
                    (json.dumps(request) + "\n").encode(),
                    cwd=tmp_path,
                    output_limit=16_384,
                )
            )["ok"]
            assert verdict["review"] is True
            reason = verdict["reason"]
            action = {
                "tool": "write_stdin",
                "environment_id": "local",
                "session_id": session.id,
                "chars": '界"\\\n',
                "cwd": str(session.terminal_info.cwd),
                "sandbox_permissions": "require_escalated",
                "tty": True,
            }

            def cost(value):
                return len(
                    json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode()
                ) + len(reason.encode())

            action["chars"] += "a" * (8000 + extra - cost(action))
            assert cost(action) == 8000 + extra
            reviewed = []

            async def decline(request):
                reviewed.append(request)
                assert request.params["_meta"]["tool_params"] == action
                assert request.params["message"] == reason
                runtime.respond_execution_approval(request.request_id, "decline")

            runtime.set_execution_approval_handler(decline)
            await call(
                runtime,
                model,
                "write_stdin",
                {"session_id": session.id, "chars": action["chars"], "yield_time_ms": 250},
            )
            assert len(reviewed) == (1 if extra == 0 else 0)
            assert not (tmp_path / "received").exists()
            assert session.id in runtime._process_manager._sessions

    asyncio.run(scenario())
