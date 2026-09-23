"""A long tool result remains bounded and does not break the next input."""

import os
import sys
from io import StringIO

import pexpect
import pyte
import pytest

PROGRAM = r"""
import asyncio
from pathlib import Path
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelItemCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools import ToolRegistry

CONTENT = "HEAD_SENTINEL\n" + chr(27) + "[2J\n" + "".join(
    f"TOOL_ROW_{i:03} abcdefghijklmnop\n" for i in range(300)) + "TAIL_SENTINEL"

async def main():
    cwd = Path.cwd()
    settings = CorkiSettings(cwd, skills_enabled=False, plugins_enabled=False)
    class Tool:
        spec = ToolSpec("long_result", "fixture", {"type": "object"})
        calls = 0
        async def execute(self, call, context):
            self.calls += 1
            return ToolResult(call.id, call.name, CONTENT)
    class Model:
        calls = 0
        async def stream(self, request):
            self.calls += 1
            turn, step = request.items[-1].turn_id, new_step_id()
            if self.calls == 1:
                yield ModelItemCompleted(ToolCallItem(
                    ToolCall(new_tool_call_id(), "long_result", {}), turn, step))
            elif self.calls == 2:
                assert any(isinstance(item, ToolResultItem) and item.content == CONTENT
                           for item in request.items)
                yield ModelCompleted((AssistantMessageItem("LONG_RESULT_ACK", turn, step),))
            else:
                yield ModelCompleted((AssistantMessageItem("FOLLOWUP_OK", turn, step),))
        async def aclose(self):
            pass
    class App(CorkiApplication):
        async def _consume_turn(self, message):
            await super()._consume_turn(message)
            print("TURN_SETTLED", flush=True)
    registry, model, tool = ToolRegistry(), Model(), Tool()
    registry.register(tool)
    runtime = await LangGraphRuntime.acreate(settings=settings, database_path=cwd / "state.db",
        model=model, registry=registry, home_path=cwd / "home")
    ui = TerminalUI(settings, cwd / "input-history")
    app = App(settings, CorkiPaths.from_home(cwd / "home"), runtime, ui)
    try:
        assert await app.run() == 0
        assert model.calls == 3 and tool.calls == 1
        source = ui._transcript.render(100)
        assert source.count("HEAD_SENTINEL") == source.count("TAIL_SENTINEL") == 1
        assert r"\x1b[2J" in source
        assert "characters omitted" in source
        assert "TOOL_ROW_150" not in source
        assert source.count("LONG_RESULT_ACK") == source.count("FOLLOWUP_OK") == 1
        assert list(ui._session.history.get_strings()) == ["first", "follow"]
    finally:
        await runtime.aclose()
    print("LONG_TOOL_VERIFIED", flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
def test_long_tool_output_keeps_history_and_next_input_usable(tmp_path, width):
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM],
        cwd=tmp_path,
        encoding="utf-8",
        timeout=15,
        dimensions=(24, width),
        env={
            **os.environ,
            "CORKI_HOME": str(tmp_path / "home"),
            "TERM": "xterm-256color",
            "PROMPT_TOOLKIT_NO_CPR": "1",
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
        },
    )
    output = StringIO()
    child.logfile_read = output
    try:
        child.expect("Ask Corki to do anything")
        child.send("first\r")
        child.expect_exact("LONG_RESULT_ACK")
        child.expect_exact("TURN_SETTLED")
        child.expect("Ask Corki to do anything")
        assert "\x1b[2J" not in output.getvalue()
        screen = pyte.Screen(width, 24)
        pyte.Stream(screen).feed(output.getvalue())
        assert "LONG_RESULT_ACK" in "\n".join(screen.display)

        child.send("follow\x14")
        child.expect_exact("\x1b[?1049h")
        child.send("\x1b[H")
        child.expect_exact("HEAD_SENTINEL")
        child.send("q")
        child.expect_exact("\x1b[?1049l")
        child.expect("follow")
        child.send("\r")
        child.expect_exact("FOLLOWUP_OK")
        child.expect_exact("TURN_SETTLED")
        child.expect("Ask Corki to do anything")
        screen = pyte.Screen(width, 24)
        pyte.Stream(screen).feed(output.getvalue())
        assert "FOLLOWUP_OK" in "\n".join(screen.display)
        child.sendcontrol("d")
        child.expect_exact("LONG_TOOL_VERIFIED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
