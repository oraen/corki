"""Tool completion/failure must preserve the active ordinary input draft."""

import os
import sys
import time
from io import StringIO

import pexpect
import pyte
import pytest
from wcwidth import wcswidth

PROGRAM = r"""
import asyncio, sys
from pathlib import Path
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.items import AssistantMessageItem, ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools import ToolRegistry

async def main():
    cwd = Path.cwd()
    release = asyncio.Event()
    class Tool:
        spec = ToolSpec("fixture_tool", "fixture", {"type": "object"})
        calls = 0
        async def execute(self, call, context):
            self.calls += 1
            await release.wait()
            return ToolResult(call.id, call.name, "TOOL_LINE\n中文结果\nTOOL_TAIL",
                              is_error=sys.argv[1] == "failed")
    class Model:
        calls = 0
        closed = False
        async def stream(self, request):
            self.calls += 1
            turn, step = request.items[-1].turn_id, new_step_id()
            if self.calls == 1:
                item = ToolCallItem(ToolCall("fixture-call", "fixture_tool", {}), turn, step)
            else:
                item = AssistantMessageItem("Response complete.", turn, step)
            yield ModelCompleted((item,))
        async def aclose(self): self.closed = True
    class UI(TerminalUI):
        def __init__(self, *args):
            super().__init__(*args)
            def edited(buffer):
                if buffer.text == "draft": release.set()
            self._session.default_buffer.on_text_changed += edited
    class App(CorkiApplication):
        async def _consume_turn(self, message):
            await super()._consume_turn(message)
            print("TURN_SETTLED", flush=True)
    settings = CorkiSettings(cwd, skills_enabled=False, plugins_enabled=False)
    registry, model, tool = ToolRegistry(), Model(), Tool()
    registry.register(tool)
    runtime = await LangGraphRuntime.acreate(settings=settings, model=model, registry=registry,
        database_path=cwd / "state.db", home_path=cwd / "home")
    ui = UI(settings, cwd / "input-history")
    assert await App(settings, CorkiPaths.from_home(cwd / "home"), runtime, ui).run() == 0
    assert tool.calls == 1 and model.calls == 3 and model.closed
    assert list(ui._session.history.get_strings()) == ["start", "draft"]
    replay = ui._transcript.render(100)
    assert replay.count("TOOL_LINE") == replay.count("TOOL_TAIL") == 1
    assert replay.index("TOOL_TAIL") < replay.index("Response complete.")
    assert ("fixture_tool failed" in replay) == (sys.argv[1] == "failed")
    print("TOOL_COMPOSER_VERIFIED", flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("outcome", ["success", "failed"])
def test_tool_result_does_not_consume_draft(tmp_path, width, outcome):
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM, outcome],
        cwd=tmp_path,
        encoding="utf-8",
        timeout=15,
        dimensions=(30, width),
        env={
            **os.environ,
            "CORKI_HOME": str(tmp_path / "home"),
            "TERM": "xterm-256color",
            "PROMPT_TOOLKIT_NO_CPR": "1",
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
        },
    )
    terminal_output = StringIO()
    child.logfile_read = terminal_output
    try:
        child.expect("Ask Corki to do anything")
        child.send("start")
        time.sleep(0.15)
        child.send("\r")
        child.expect("fixture_tool")
        child.send("draft")
        child.expect("TOOL_LINE")
        child.expect("TOOL_TAIL")
        if outcome == "failed":
            child.expect("fixture_tool failed")
        child.expect("TURN_SETTLED")
        child.expect("draft")
        screen = pyte.Screen(width, 30)
        stream = pyte.Stream(screen)
        stream.feed(terminal_output.getvalue())
        visible = "\n".join(screen.display)
        for marker in ("TOOL_LINE", "中文结果", "TOOL_TAIL"):
            assert visible.count(marker) == 1, visible
        assert [row.strip() for row in screen.display if "› draft" in row][-1] == "› draft"
        assert ("fixture_tool failed" in visible) == (outcome == "failed")
        assert visible.index("TOOL_TAIL") < visible.index("Response complete.")
        assert all(wcswidth(row) <= width for row in screen.display)

        read_at = len(terminal_output.getvalue())
        resized_width = 100 if width == 40 else 40
        child.setwinsize(30, resized_width)
        child.expect_exact("\x1b[3J\x1b[2J\x1b[H")
        child.expect("draft")
        screen.resize(lines=30, columns=resized_width)
        stream.feed(terminal_output.getvalue()[read_at:])
        resized = "\n".join(screen.display)
        for marker in ("TOOL_LINE", "中文结果", "TOOL_TAIL"):
            assert resized.count(marker) == 1, resized
        assert [row.strip() for row in screen.display if "› draft" in row][-1] == "› draft"
        assert ("fixture_tool failed" in resized) == (outcome == "failed")
        assert resized.index("TOOL_TAIL") < resized.index("Response complete.")
        assert all(wcswidth(row) <= resized_width for row in screen.display)

        child.send("\r")
        child.expect("TURN_SETTLED")
        child.expect("Ask Corki to do anything")
        child.sendcontrol("d")
        child.expect("TOOL_COMPOSER_VERIFIED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
