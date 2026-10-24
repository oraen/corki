"""Real ConPTY acceptance; needs a newly built native helper, not a mocked PTY."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from corki.cli.paste_burst import ENTER_WINDOW
from corki.platforms.processes import close_job, create_subprocess_exec

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="requires real Windows ConPTY")


@pytest.fixture
def helper():
    configured = os.environ.get("CORKI_TEST_NATIVE_TERMINAL")
    if not configured or not Path(configured).is_file():
        pytest.fail("Build native/sandbox and set CORKI_TEST_NATIVE_TERMINAL to its executable")
    return Path(configured).resolve()


async def start(helper, directory, script, *, unbuffered=True):
    process = await create_subprocess_exec(
        str(helper), "--corki-terminal-process", cwd=directory,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    flags = ["-u"] if unbuffered else []
    frame = {"command": [sys.executable, "-X", "utf8", *flags, "-c", script],
             "cwd": str(directory), "env": dict(os.environ)}
    try:
        process.stdin.write(json.dumps(frame).encode("utf-8") + b"\n")
        await process.stdin.drain()
    except BaseException:
        await cleanup(process)
        raise
    return process


async def cleanup(process):
    close_job(process)
    if process.returncode is None:
        process.kill()
    process._transport.close()
    await process.wait()


def test_conpty_interactive_unicode_and_exit_tail(helper, tmp_path):
    directory = tmp_path / "终端 测试"
    directory.mkdir()

    async def scenario():
        # Output exceeds the OS pipe buffer and must be drained concurrently.
        script = (
            "import sys; print('READY_INPUT', flush=True); value=input(); "
            "print('RECEIVED:'+value, flush=True); "
            "print('x'*300000, flush=True); print('FINAL_TAIL', flush=True)"
        )
        # CPython 3.12's raw WindowsConsoleIO performs partial writes. With -u,
        # TextIOWrapper can discard the remainder of a large console write.
        # Retain BufferedWriter so the producer really emits all 300000 bytes;
        # explicit flushes below still exercise concurrent terminal draining.
        process = await start(helper, directory, script, unbuffered=False)
        try:
            async with asyncio.timeout(60):
                assert json.loads(await process.stdout.readline()) == {"ready": True, "version": 1}
                await process.stdout.readuntil(b"READY_INPUT")
                process.stdin.write("你好 terminal\n".encode())
                await process.stdin.drain()
                output = await process.stdout.read()
                assert await process.wait() == 0
                assert "RECEIVED:你好 terminal".encode() in output
                assert b"FINAL_TAIL" in output
                assert output.count(b"x") >= 300000
        finally:
            await cleanup(process)

    asyncio.run(scenario())


def test_conpty_parent_disconnect_terminates_session(helper, tmp_path):
    async def scenario():
        process = await start(
            helper, tmp_path, "import time; print('RUNNING',flush=True); time.sleep(120)"
        )
        try:
            async with asyncio.timeout(30):
                assert json.loads(await process.stdout.readline())["ready"] is True
                await process.stdout.readuntil(b"RUNNING")
                process.stdin.close()
                await process.stdout.read()
                await process.wait()
        finally:
            await cleanup(process)

    asyncio.run(scenario())


def test_conpty_quick_exit_keeps_output(helper, tmp_path):
    async def scenario():
        for _ in range(10):
            process = await start(helper, tmp_path, "print('FAST_EXIT_TAIL',flush=True)")
            try:
                async with asyncio.timeout(30):
                    assert json.loads(await process.stdout.readline())["ready"] is True
                    assert b"FAST_EXIT_TAIL" in await process.stdout.read()
                    assert await process.wait() == 0
            finally:
                await cleanup(process)

    asyncio.run(scenario())


def test_native_cli_status_and_normal_exit(helper, tmp_path):
    """Exercise the actual CLI under ConPTY, without a model request."""
    directory = tmp_path / "CLI 中文 workspace"
    directory.mkdir()
    home = tmp_path / "isolated-home"
    script = (
        "import os,sys,runpy; "
        f"os.environ['CORKI_HOME']={str(home)!r}; "
        "os.environ.pop('CORKI_API_KEY',None); "
        "os.environ.pop('OPENAI_API_KEY',None); "
        "os.environ['PROMPT_TOOLKIT_NO_CPR']='1'; "
        "sys.argv=['corki']; runpy.run_module('corki',run_name='__main__')"
    )

    async def scenario():
        process = await start(helper, directory, script)
        try:
            async with asyncio.timeout(90):
                assert json.loads(await process.stdout.readline())["ready"] is True
                await process.stdout.readuntil(b"Ask Corki to do anything")
                process.stdin.write(b"/status")
                await process.stdin.drain()
                await process.stdout.readuntil(b"status")
                # One write containing text + Enter is a non-bracketed paste.
                # Submit only after the real composer's paste guard expires.
                await asyncio.sleep(ENTER_WINDOW + 0.1)
                process.stdin.write(b"\r")
                await process.stdin.drain()
                await process.stdout.readuntil(b"Directory:")
                await process.stdout.readuntil(b"Ask Corki to do anything")
                process.stdin.write(b"\x04")
                await process.stdin.drain()
                tail = await process.stdout.read()
                assert b"Session ended" in tail
                assert await process.wait() == 0
        finally:
            await cleanup(process)

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_first", [False, True])
def test_native_cli_fake_model_stream_and_exit(helper, tmp_path, cancel_first):
    """Run a full CLI turn through the native console and local fake provider."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    requests = []
    release_partial = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            assert self.path.endswith("/chat/completions")
            assert body["model"] == "fixture-model"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            partial = cancel_first and len(requests) == 1
            chunk = {"choices": [{"index": 0, "delta": {
                # The CLI commits complete source lines while SSE is open.
                "content": "WINDOWS_CLI_PARTIAL\n" if partial else "WINDOWS_CLI_RESULT"
            }, "finish_reason": None if partial else "stop"}]}
            ending = "\n\n" if partial else "\n\ndata: [DONE]\n\n"
            self.wfile.write(("data: " + json.dumps(chunk) + ending).encode())
            self.wfile.flush()
            if partial:
                # Hold a real SSE response open until the CLI has handled Ctrl+C.
                # A completed response would only test interrupting an idle prompt.
                release_partial.wait(120)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    directory = tmp_path / "CLI 中文 workspace"
    directory.mkdir()
    home = tmp_path / "isolated-home"
    home.mkdir()
    config = {
        "default": {"provider": "fixture", "model": "fixture-model"},
        "providers": {"fixture": {
            "base_url": f"http://127.0.0.1:{server.server_port}/v1",
            "api_mode": "chat_completions", "api_key": "fixture-not-a-credential",
            "models": {"fixture-model": {"context_window": 262144}},
        }},
        "features": {"goals": False}, "skills": {"enabled": False},
    }
    (home / "settings.json").write_text(json.dumps(config), encoding="utf-8")
    script = (
        "import os,sys,runpy; "
        f"os.environ['CORKI_HOME']={str(home)!r}; "
        "os.environ['PROMPT_TOOLKIT_NO_CPR']='1'; "
        "sys.argv=['corki']; runpy.run_module('corki',run_name='__main__')"
    )

    async def scenario():
        process = await start(helper, directory, script)
        try:
            async with asyncio.timeout(120):
                assert json.loads(await process.stdout.readline())["ready"] is True
                await process.stdout.readuntil(b"Ask Corki to do anything")
                process.stdin.write(b"Say hello")
                await process.stdin.drain()
                await asyncio.sleep(ENTER_WINDOW + 0.1)
                process.stdin.write(b"\r")
                await process.stdin.drain()
                if cancel_first:
                    await process.stdout.readuntil(b"WINDOWS_CLI_PARTIAL")
                    process.stdin.write(b"\x03")
                    await process.stdin.drain()
                    await process.stdout.readuntil(b"Turn interrupted.")
                    release_partial.set()
                    await process.stdout.readuntil(b"Ask Corki to do anything")
                    await asyncio.sleep(0.5)
                    process.stdin.write(b"Continue after stop")
                    await process.stdin.drain()
                    await asyncio.sleep(ENTER_WINDOW + 0.1)
                    process.stdin.write(b"\r")
                    await process.stdin.drain()
                await process.stdout.readuntil(b"WINDOWS_CLI_RESULT")
                await process.stdout.readuntil(b"Ask Corki to do anything")
                await asyncio.sleep(0.5)
                process.stdin.write(b"\x04")
                await process.stdin.drain()
                try:
                    await asyncio.wait_for(process.stdout.readuntil(b"Session ended"), 15)
                except TimeoutError:
                    # A redraw can contain the placeholder before prompt-toolkit
                    # starts its next reader; retry only the harmless EOF key.
                    process.stdin.write(b"\x04")
                    await process.stdin.drain()
                    await process.stdout.readuntil(b"Session ended")
                assert await process.wait() == 0
                assert len(requests) == (2 if cancel_first else 1)
                if cancel_first:
                    last_user = next(
                        message for message in reversed(requests[-1]["messages"])
                        if message["role"] == "user"
                    )
                    content = last_user["content"]
                    if isinstance(content, list):
                        content = "".join(
                            part["text"] for part in content if part["type"] == "text"
                        )
                    assert content == "Continue after stop"
        finally:
            release_partial.set()
            await cleanup(process)
            server.shutdown()
            server.server_close()

    asyncio.run(scenario())


def test_native_composer_multiline_and_input_close(helper, tmp_path):
    """Use the real Windows console adapter, including its Ctrl+J fallback."""
    script = """
import asyncio, json, os
from pathlib import Path
from corki.cli.terminal import TerminalUI
from corki.config import CorkiSettings
os.environ['PROMPT_TOOLKIT_NO_CPR'] = '1'
async def main():
    cwd = Path.cwd()
    ui = TerminalUI(CorkiSettings(cwd), cwd / 'history')
    message = await ui.read_message()
    print('MESSAGE=' + json.dumps(message), flush=True)
    try:
        await ui.read_message()
    except (EOFError, KeyboardInterrupt):
        print('INPUT_CLOSED', flush=True)
asyncio.run(main())
"""

    async def scenario():
        process = await start(helper, tmp_path, script)
        try:
            async with asyncio.timeout(90):
                assert json.loads(await process.stdout.readline())["ready"] is True
                await process.stdout.readuntil(b"Ask Corki to do anything")
                process.stdin.write(b"first\nsecond")
                await process.stdin.drain()
                await process.stdout.readuntil(b"second")
                await asyncio.sleep(ENTER_WINDOW + 0.1)
                process.stdin.write(b"\r")
                await process.stdin.drain()
                await process.stdout.readuntil(b'MESSAGE="first\\nsecond"')
                await process.stdout.readuntil(b"Ask Corki to do anything")
                process.stdin.write(b"\x04")
                await process.stdin.drain()
                assert b"INPUT_CLOSED" in await process.stdout.read()
                assert await process.wait() == 0
        finally:
            await cleanup(process)

    asyncio.run(scenario())


def test_manager_write_stdin_uses_real_terminal(helper, tmp_path, monkeypatch):
    from corki.tools.builtin import process as module

    # Select the freshly built acceptance artifact; retain the real transport,
    # shell, process containment, session state and write_stdin implementation.
    monkeypatch.setattr(module, "bundled_compiler", lambda: helper)

    async def scenario():
        manager = module.ProcessManager()
        try:
            async with asyncio.timeout(90):
                result = await manager.execute(
                    "Write-Output 'INPUT_READY'; $value=Read-Host; "
                    "Write-Output ('RECEIVED:'+$value)",
                    cwd=tmp_path, tty=True, login=False, yield_seconds=0.1,
                )
                output = result.output
                while "INPUT_READY" not in output:
                    assert result.session_id is not None and result.exit_code is None
                    result = await manager.write_stdin(
                        result.session_id, "", yield_seconds=0.1
                    )
                    output += result.output
                assert result.session_id is not None
                result = await manager.write_stdin(
                    result.session_id, "你好 terminal\n", yield_seconds=0.1
                )
                output += result.output
                while result.exit_code is None:
                    result = await manager.write_stdin(
                        result.session_id, "", yield_seconds=0.1
                    )
                    output += result.output
                assert result.exit_code == 0
                assert "RECEIVED:你好 terminal" in output
        finally:
            await manager.terminate_all()

    asyncio.run(scenario())


@pytest.mark.parametrize("tty", [False, True])
def test_targeted_stop_preserves_sibling_and_manager_reuse(helper, tmp_path, monkeypatch, tty):
    from corki.tools.builtin import process as module

    monkeypatch.setattr(module, "bundled_compiler", lambda: helper)

    async def scenario():
        manager = module.ProcessManager()
        children = []
        try:
            async with asyncio.timeout(90):
                for index in range(2):
                    result = await manager.execute(
                        "Write-Output 'OWNED_READY'; Start-Sleep -Seconds 120",
                        cwd=tmp_path, tty=tty, login=False, yield_seconds=0.1,
                        item_id=f"owned-{index}",
                    )
                    output = result.output
                    while "OWNED_READY" not in output:
                        assert result.session_id is not None and result.exit_code is None
                        result = await manager.write_stdin(
                            result.session_id, "", yield_seconds=0.1
                        )
                        output += result.output
                    children.append(manager._sessions[result.session_id])
                assert await manager.terminate_background_terminal(children[0].id)
                assert children[0].process.returncode is not None
                assert children[1].process.returncode is None
                assert not await manager.terminate_background_terminal(children[0].id)
                await manager.terminate_all()
                assert not manager._sessions
                assert children[1].process.returncode is not None
                result = await manager.execute(
                    "Write-Output 'REUSABLE'", cwd=tmp_path, tty=tty,
                    login=False, yield_seconds=0.1,
                )
                output = result.output
                while result.exit_code is None:
                    result = await manager.write_stdin(result.session_id, "", yield_seconds=0.1)
                    output += result.output
                assert result.exit_code == 0 and "REUSABLE" in output
        finally:
            await manager.terminate_all()

    asyncio.run(scenario())
