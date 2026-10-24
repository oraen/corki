"""Exercise the terminal handshake against real pipes, independent of ConPTY."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from corki.platforms import terminal
from corki.platforms.processes import create_subprocess_exec


def runner(monkeypatch, script):
    children = []

    async def spawn(*args, **kwargs):
        assert args[1] == "--corki-terminal-process"
        child = await create_subprocess_exec(
            sys.executable, "-X", "utf8", "-u", "-c", script, **kwargs
        )
        children.append(child)
        return child

    monkeypatch.setattr(terminal, "create_subprocess_exec", spawn)
    return children


def test_handshake_preserves_output_and_interactive_input(tmp_path, monkeypatch):
    runner(
        monkeypatch,
        "import sys,json; request=json.loads(sys.stdin.readline()); "
        "assert request['env']=={'FIXTURE':'中文'}; "
        "print(json.dumps({'ready':True,'version':1}),flush=True); "
        "print('FIRST',flush=True); print(sys.stdin.readline().strip(),flush=True)",
    )

    async def scenario():
        process = await terminal.spawn_terminal(
            Path(sys.executable), ["fixture", "a b"], cwd=tmp_path, env={"FIXTURE": "中文"}
        )
        try:
            assert (await process.stdout.readline()).rstrip(b"\r\n") == b"FIRST"
            process.stdin.write("下一行\n".encode())
            await process.stdin.drain()
            assert (await process.stdout.readline()).decode().strip() == "下一行"
            assert await process.wait() == 0
        finally:
            await terminal._discard(process)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "header",
    [
        {"ready": False, "version": 1},
        {"ready": 1, "version": 1},
        {"ready": True, "version": True},
        {"ready": True, "version": 2},
        {"ready": True, "version": 1, "extra": True},
        [],
    ],
)
def test_invalid_acknowledgment_reaps_child(tmp_path, monkeypatch, header):
    children = runner(
        monkeypatch,
        f"import sys,time; sys.stdin.readline(); print({json.dumps(header)!r},"
        "flush=True); time.sleep(120)",
    )

    async def scenario():
        with pytest.raises(OSError, match="protocol mismatch"):
            await terminal.spawn_terminal(Path(sys.executable), ["fixture"], cwd=tmp_path, env={})
        assert len(children) == 1 and children[0].returncode is not None

    asyncio.run(scenario())


def test_startup_timeout_reaps_child(tmp_path, monkeypatch):
    children = runner(monkeypatch, "import sys,time; sys.stdin.readline(); time.sleep(120)")

    async def scenario():
        with pytest.raises(TimeoutError):
            await terminal.spawn_terminal(
                Path(sys.executable), ["fixture"], cwd=tmp_path, env={}, startup_timeout=0.2
            )
        assert children and children[0].returncode is not None

    asyncio.run(scenario())


def test_startup_eof_reports_diagnostic_and_reaps_child(tmp_path, monkeypatch):
    children = runner(
        monkeypatch,
        "import sys; sys.stdin.readline(); print('ConPTY unavailable',file=sys.stderr)",
    )

    async def scenario():
        with pytest.raises(OSError, match="ConPTY unavailable"):
            await terminal.spawn_terminal(Path(sys.executable), ["fixture"], cwd=tmp_path, env={})
        assert children[0].returncode is not None

    asyncio.run(scenario())


def test_cancelled_startup_reaps_owned_child(tmp_path, monkeypatch):
    children = runner(monkeypatch, "import sys,time; sys.stdin.readline(); time.sleep(120)")

    async def scenario():
        request = asyncio.create_task(
            terminal.spawn_terminal(Path(sys.executable), ["fixture"], cwd=tmp_path, env={})
        )
        async with asyncio.timeout(10):
            while not children:
                await asyncio.sleep(0.01)
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
        assert children[0].returncode is not None

    asyncio.run(scenario())


def test_cancellation_during_process_creation_joins_late_child(tmp_path, monkeypatch):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        children = []

        async def delayed_spawn(*args, **kwargs):
            child = await create_subprocess_exec(
                sys.executable, "-c", "import time; time.sleep(120)", **kwargs
            )
            children.append(child)
            entered.set()
            await release.wait()
            return child

        monkeypatch.setattr(terminal, "create_subprocess_exec", delayed_spawn)
        request = asyncio.create_task(
            terminal.spawn_terminal(Path(sys.executable), ["fixture"], cwd=tmp_path, env={})
        )
        try:
            async with asyncio.timeout(10):
                await entered.wait()
                request.cancel()
                await asyncio.sleep(0)
                assert not request.done()
                request.cancel()  # A repeated stop must not cancel the cleanup owner.
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await request
            assert children[0].returncode is not None
        finally:
            release.set()
            for child in children:
                await terminal._discard(child)

    asyncio.run(scenario())
