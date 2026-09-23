"""Tool execution can finish during an assistant item without splitting its display."""

import os
import sys

import pexpect
import pytest

PROGRAM = r"""
import asyncio
from pathlib import Path
from corki.cli.terminal_console import TerminalConsole
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelItemCompleted, ModelTextDelta
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolConcurrency, ToolResult, ToolSpec
from corki.tools import ToolRegistry

async def main():
    finished = asyncio.Event()
    class Tool:
        spec = ToolSpec("during_answer", "fixture", {"type": "object"},
                        concurrency=ToolConcurrency.PARALLEL)
        calls = 0
        async def execute(self, call, context):
            self.calls += 1
            finished.set()
            return ToolResult(call.id, call.name, "TOOL_EVIDENCE")
    class Model:
        calls = 0
        closed = False
        async def stream(self, request):
            self.calls += 1
            turn, step = request.items[-1].turn_id, new_step_id()
            if self.calls == 2:
                assert sum(isinstance(item, ToolResultItem) for item in request.items) == 1
                yield ModelCompleted((AssistantMessageItem("Finished", turn, step),))
                return
            message = AssistantMessageItem("Opening line\nunfinished Closing line", turn, step)
            call = ToolCallItem(ToolCall(new_tool_call_id(), "during_answer", {}), turn, step)
            yield ModelTextDelta("Opening line\nunfinished ", message.id)
            yield ModelItemCompleted(call)
            await finished.wait()
            print("TOOL_EXECUTED", flush=True)
            await asyncio.to_thread(input)
            yield ModelTextDelta("Closing line", message.id)
            yield ModelCompleted((message, call))
        async def aclose(self):
            self.closed = True
    cwd = Path.cwd()
    settings = CorkiSettings(working_directory=cwd, skills_enabled=False, plugins_enabled=False)
    registry, model, tool = ToolRegistry(), Model(), Tool()
    registry.register(tool)
    runtime = await LangGraphRuntime.acreate(settings=settings, database_path=cwd / "sessions.db",
        model=model, registry=registry, home_path=cwd / "home")
    ui = TerminalUI(settings, cwd / "history", console=TerminalConsole(color_system=None))
    app = CorkiApplication(settings, CorkiPaths.from_home(cwd / "home"), runtime, ui)
    try:
        await app._consume_events(runtime.stream("Run"))
        assert model.calls == 2 and tool.calls == 1
        for width in (40, 100):
            source = ui._transcript.render(width)
            assert source.count("Opening line") == source.count("Closing line") == 1
            assert source.count("during_answer") == source.count("TOOL_EVIDENCE") == 1
            assert source.index("Closing line") < source.index("during_answer")
    finally:
        await runtime.aclose()
    assert model.closed
    print("INTERLEAVED_CLOSED", flush=True)

asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
def test_interleaved_tool_waits_for_authoritative_body_on_terminal(tmp_path, width):
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM],
        cwd=tmp_path,
        encoding="utf-8",
        timeout=10,
        dimensions=(30, width),
        env={
            **os.environ,
            "CORKI_HOME": str(tmp_path / "home"),
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
            "TERM": "xterm-256color",
            "PROMPT_TOOLKIT_NO_CPR": "1",
        },
    )
    try:
        child.expect_exact("TOOL_EXECUTED")
        before = child.before
        assert "during_answer" not in before and "TOOL_EVIDENCE" not in before
        assert "unfinished" not in before
        child.sendline("")
        child.expect_exact("INTERLEAVED_CLOSED")
        after = child.before
        assert "\x1b[3J" not in after and "\x1b[2J" not in after
        for text in ("Opening line", "unfinished Closing line", "during_answer", "TOOL_EVIDENCE"):
            assert (before + after).count(text) == 1
        assert (
            after.index("Closing line")
            < after.index("during_answer")
            < after.index("TOOL_EVIDENCE")
        )
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
