"""Manager/transport contract; real ConPTY acceptance lives in integration tests."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from corki.tools.builtin import process as module


def test_windows_terminal_preserves_prepared_command_and_input(tmp_path, monkeypatch):
    manager = module.ProcessManager()
    compiler = Path(sys.executable)
    prepared = ["reviewed-program", "argument with spaces"]
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(module, "bundled_compiler", lambda: compiler)

    async def spawn(binary, argv, *, cwd, env):
        assert binary == compiler and argv == prepared and cwd == tmp_path
        assert env == {"TEST": "value"}
        # A real pipe exercises queued write/drain and reader/cleanup ownership.
        # It does not claim to emulate terminal behavior or Windows containment.
        return await asyncio.create_subprocess_exec(
            sys.executable, "-X", "utf8", "-u", "-c",
            "import sys; print('READY',flush=True); print(input(),flush=True)",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

    monkeypatch.setattr(module, "spawn_terminal", spawn)

    async def scenario():
        session = await manager._start_session(
            "original command", tmp_path, None, True, False, None,
            prepared_argv=prepared, env={"TEST": "value"}, publish=False,
        )
        try:
            assert session.tty and session.pty_master_fd is None
            async with asyncio.timeout(5):
                while "READY" not in session.peek_output():
                    await asyncio.sleep(0.01)
                result = await manager._write_stdin(session, "你好\n", 1)
                output = result.output
                while result.exit_code is None:
                    result = await manager._write_stdin(session, "", 0.1)
                    output += result.output
                assert result.exit_code == 0 and "你好" in output
        finally:
            await manager._retire(session)
        assert session.writer_task.done() and session.reader_task.done()

    asyncio.run(scenario())


def test_windows_tty_missing_helper_does_not_silently_use_pipes(tmp_path, monkeypatch):
    manager = module.ProcessManager()
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(module, "bundled_compiler", lambda: None)

    async def scenario():
        with pytest.raises(ValueError, match="bundled Corki terminal helper"):
            await manager._start_session("unused", tmp_path, None, True, False, None)
        assert not manager._sessions

    asyncio.run(scenario())
