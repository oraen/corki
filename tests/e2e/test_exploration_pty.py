"""Real process results, grouped CLI display and terminal cleanup without a model service."""

import io
import os
import sys
import time

import pexpect
import pytest

PROGRAM = r"""
import asyncio, io, sys
from pathlib import Path
from rich.console import Console
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall

async def main():
    cwd = Path.cwd()
    class Model:
        count = 0
        async def stream(self, request):
            self.count += 1
            turn, step = request.items[-1].turn_id, new_step_id()
            if self.count <= 2:
                path = "alpha" if self.count == 1 else sys.argv[1]
                command = "cat " + path
                if sys.argv[2] == "compound":
                    command = "cd . && " + command
                    if path != "absent":
                        command += " | head -n 20"
                call = ToolCall(str(self.count), "exec_command",
                                {"cmd": command, "login": False})
                yield ModelCompleted((ToolCallItem(call, turn, step),))
            else:
                yield ModelCompleted((AssistantMessageItem("EXPLORATION_DONE", turn, step),))
        async def aclose(self): pass
    settings = CorkiSettings(cwd, skills_enabled=False, plugins_enabled=False,
                             execution_permissions=None)
    runtime = await LangGraphRuntime.acreate(settings=settings, model=Model(),
                    database_path=cwd / "state.db", home_path=cwd / "home")
    ui = TerminalUI(settings, cwd / "history")
    class App(CorkiApplication):
        async def _consume_turn(self, message):
            await super()._consume_turn(message)
            assert ui._exploration is None
            compact = ui._transcript.render(100)
            expanded = ui._transcript.render(100, expand_tools=True)
            assert "PRIVATE_FILE_CONTENT" not in compact
            assert "PRIVATE_FILE_CONTENT" in expanded
            assert compact.index("Explored") < compact.index("EXPLORATION_DONE")
            print("TURN_SETTLED", flush=True)
    app = App(settings, CorkiPaths.from_home(cwd / "home"), runtime, ui)
    assert await app.run() == 0
    assert not runtime._process_manager._sessions
    class NeverModel:
        async def stream(self, request):
            raise AssertionError("history replay must not sample a model")
            yield
        async def aclose(self): pass
    cold = await LangGraphRuntime.acreate(settings=settings, model=NeverModel(),
        database_path=cwd / "state.db", home_path=cwd / "home", thread_id=runtime.thread_id)
    try:
        snapshot = await cold.load_display_snapshot()
        results = [i for i in snapshot.items if isinstance(i, ToolResultItem)]
        assert [r.exit_code for r in results] == [0, 1 if sys.argv[1] == "absent" else 0]
        restored = TerminalUI(settings, cwd / "history", console=Console(file=io.StringIO()))
        restored.replay_history(snapshot)
        compact = restored._transcript.render(100)
        expanded = restored._transcript.render(100, expand_tools=True)
        assert "Read alpha, " + sys.argv[1] in compact
        assert "PRIVATE_FILE_CONTENT" not in compact and "PRIVATE_FILE_CONTENT" in expanded
        if sys.argv[1] == "absent":
            assert "Command failed (exit 1)" in compact
        assert not cold._process_manager._sessions
    finally:
        await cold.aclose()
    print("CLEAN_EXIT", flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("second", ["beta", "absent"])
@pytest.mark.parametrize("style", ["simple", "compound"])
def test_exploration_groups_actual_reads_and_reports_real_exit(tmp_path, width, second, style):
    (tmp_path / "alpha").write_text("PRIVATE_FILE_CONTENT\n")
    (tmp_path / "beta").write_text("SECOND_CONTENT\n")
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM, second, style],
        cwd=tmp_path,
        env={
            **os.environ,
            "TERM": "xterm-256color",
            "PROMPT_TOOLKIT_NO_CPR": "1",
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
        },
        encoding="utf-8",
        dimensions=(30, width),
        timeout=20,
    )
    captured = io.StringIO()
    child.logfile_read = captured
    try:
        child.expect("Ask Corki to do anything")
        child.send("inspect")
        time.sleep(0.15)
        child.send("\r")
        child.expect_exact("Explored")
        child.expect_exact("alpha, " + second)
        if second == "absent":
            child.expect_exact("Command failed (exit 1)")
        child.expect_exact("EXPLORATION_DONE")
        child.expect_exact("TURN_SETTLED")
        child.expect("Ask Corki to do anything")
        child.sendcontrol("d")
        child.expect_exact("CLEAN_EXIT")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    except pexpect.TIMEOUT:
        pytest.fail(repr(captured.getvalue()))
    finally:
        if child.isalive():
            child.terminate(force=True)
