"""A quiet model request shows live activity without leaving a stale status."""

import os
import re
import sys
import time
from contextlib import suppress
from io import StringIO

import pexpect
import pyte
import pytest


def wait_for_running_clock(child, output, width):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with suppress(pexpect.TIMEOUT):
            child.read_nonblocking(4096, timeout=0.2)
        screen = pyte.Screen(width, 24)
        pyte.Stream(screen).feed(output.getvalue())
        for row, line in enumerate(screen.display):
            match = re.search(r"Working \((\d+)s", line)
            if match and int(match[1]) >= 1:
                composer = next(
                    index
                    for index, text in enumerate(screen.display)
                    if "Ask Corki to do anything" in text
                )
                assert row < composer
                assert screen.buffer[row][line.index("Working")].bold
                status_row = next(
                    index
                    for index, text in enumerate(screen.display)
                    if "gpt-5" in text and " · " in text
                )
                assert status_row > composer
                status = screen.display[status_row]
                assert screen.buffer[status_row][status.index("gpt-5")].fg == "f6e2b7"
                assert screen.buffer[status_row][status.index(" · ") + 3].fg == "abdfa7"
                return screen
    pytest.fail("Working clock did not advance above the composer without model output")


PROGRAM = r"""
import asyncio
from pathlib import Path
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.items import AssistantMessageItem, new_step_id

async def main():
    cwd = Path.cwd()
    release = asyncio.Event()
    settings = CorkiSettings(cwd, skills_enabled=False, plugins_enabled=False)
    class Model:
        calls = 0
        async def stream(self, request):
            self.calls += 1
            await release.wait()
            yield ModelCompleted((AssistantMessageItem(
                "QUIET_MODEL_ANSWER", request.items[-1].turn_id, new_step_id()),))
        async def aclose(self):
            pass
    class UI(TerminalUI):
        def __init__(self):
            super().__init__(settings, cwd / "input-history")
            def edited(buffer):
                if buffer.text == "go":
                    release.set()
            self._session.default_buffer.on_text_changed += edited
    class App(CorkiApplication):
        async def _consume_turn(self, message):
            await super()._consume_turn(message)
            print("TURN_SETTLED", flush=True)
    model = Model()
    runtime = await LangGraphRuntime.acreate(settings=settings, database_path=cwd / "state.db",
        model=model, home_path=cwd / "home")
    ui = UI()
    app = App(settings, CorkiPaths.from_home(cwd / "home"), runtime, ui)
    assert await app.run() == 0
    assert model.calls == 1 and not ui._turn_active
    assert list(ui._session.history.get_strings()) == ["first", "go"]
    print("WORKING_STATUS_VERIFIED", flush=True)
asyncio.run(main())
"""


TOOL_PROGRAM = r"""
import asyncio
from pathlib import Path
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelItemCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools import ToolRegistry

async def main():
    cwd = Path.cwd()
    release = asyncio.Event()
    settings = CorkiSettings(cwd, skills_enabled=False, plugins_enabled=False)
    class Tool:
        spec = ToolSpec("slow_tool", "fixture", {"type": "object"})
        calls = 0
        async def execute(self, call, context):
            self.calls += 1
            await release.wait()
            return ToolResult(call.id, call.name, "TOOL_DONE")
    class Model:
        calls = 0
        async def stream(self, request):
            self.calls += 1
            turn, step = request.items[-1].turn_id, new_step_id()
            if self.calls == 1:
                yield ModelItemCompleted(ToolCallItem(
                    ToolCall(new_tool_call_id(), "slow_tool", {}), turn, step))
            else:
                yield ModelCompleted((AssistantMessageItem("TOOL_ANSWER", turn, step),))
        async def aclose(self):
            pass
    class UI(TerminalUI):
        def __init__(self):
            super().__init__(settings, cwd / "input-history")
            def edited(buffer):
                if buffer.text == "go":
                    release.set()
            self._session.default_buffer.on_text_changed += edited
    class App(CorkiApplication):
        async def _consume_turn(self, message):
            await super()._consume_turn(message)
            print("TOOL_TURN_SETTLED", flush=True)
    registry, model, tool = ToolRegistry(), Model(), Tool()
    registry.register(tool)
    runtime = await LangGraphRuntime.acreate(settings=settings, database_path=cwd / "state.db",
        model=model, registry=registry, home_path=cwd / "home")
    ui = UI()
    app = App(settings, CorkiPaths.from_home(cwd / "home"), runtime, ui)
    assert await app.run() == 0
    assert model.calls == 2 and tool.calls == 1 and not ui._active_tools
    assert list(ui._session.history.get_strings()) == ["first", "go"]
    print("TOOL_STATUS_VERIFIED", flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
def test_quiet_model_shows_working_then_restores_idle_toolbar(tmp_path, width):
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM],
        cwd=tmp_path,
        encoding="utf-8",
        timeout=10,
        dimensions=(24, width),
        env={
            **{key: value for key, value in os.environ.items() if key != "NO_COLOR"},
            "CORKI_HOME": str(tmp_path / "home"),
            "TERM": "xterm-256color",
            "PROMPT_TOOLKIT_COLOR_DEPTH": "DEPTH_24_BIT",
            "PROMPT_TOOLKIT_NO_CPR": "1",
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
        },
    )
    output = StringIO()
    child.logfile_read = output
    try:
        child.expect("Ask Corki to do anything")
        child.send("first")
        time.sleep(0.15)
        child.send("\r")
        child.expect("Working")
        assert "QUIET_MODEL_ANSWER" not in output.getvalue()
        wait_for_running_clock(child, output, width)
        child.send("go")
        child.expect_exact("QUIET_MODEL_ANSWER")
        child.expect_exact("TURN_SETTLED")
        child.expect_exact("go")
        screen = pyte.Screen(width, 24)
        pyte.Stream(screen).feed(output.getvalue())
        assert "Working" not in "\n".join(screen.display)
        assert sum("› go" in line for line in screen.display) == 1
        child.sendcontrol("c")
        child.expect("Ask Corki to do anything")
        child.sendcontrol("d")
        child.expect_exact("WORKING_STATUS_VERIFIED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)


@pytest.mark.parametrize("width", [40, 100])
def test_slow_tool_status_is_visible_and_clears_before_next_input(tmp_path, width):
    child = pexpect.spawn(
        sys.executable,
        ["-c", TOOL_PROGRAM],
        cwd=tmp_path,
        encoding="utf-8",
        timeout=15,
        dimensions=(24, width),
        env={
            **{key: value for key, value in os.environ.items() if key != "NO_COLOR"},
            "CORKI_HOME": str(tmp_path / "home"),
            "TERM": "xterm-256color",
            "PROMPT_TOOLKIT_COLOR_DEPTH": "DEPTH_24_BIT",
            "PROMPT_TOOLKIT_NO_CPR": "1",
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
        },
    )
    output = StringIO()
    child.logfile_read = output
    try:
        child.expect("Ask Corki to do anything")
        child.send("first")
        time.sleep(0.15)
        child.send("\r")
        child.expect("Running slow_tool")
        wait_for_running_clock(child, output, width)
        screen = pyte.Screen(width, 24)
        pyte.Stream(screen).feed(output.getvalue())
        assert "Running slow_tool" in "\n".join(screen.display)
        assert "TOOL_ANSWER" not in output.getvalue()
        child.send("go")
        child.expect_exact("TOOL_ANSWER")
        child.expect_exact("TOOL_TURN_SETTLED")
        screen = pyte.Screen(width, 24)
        pyte.Stream(screen).feed(output.getvalue())
        assert "Running slow_tool" not in "\n".join(screen.display)
        child.sendcontrol("c")
        child.expect("Ask Corki to do anything")
        child.sendcontrol("d")
        child.expect_exact("TOOL_STATUS_VERIFIED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
