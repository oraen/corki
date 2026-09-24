"""Real Runtime plan events retain URL bytes through a resized PTY."""

import os
import re
import sys

import pexpect
import pytest

NOTE_URL = "example.test/api/v1/projects/alpha/releases/2026/builds/1234567890"
STEP_URL = "https://example.test/api/v1/projects/beta/releases/2026/artifacts/performance/report"

BOOTSTRAP = r"""
import asyncio
import json
from pathlib import Path
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.cli.terminal_console import TerminalConsole
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall

class Model:
    count = 0
    closed = False
    async def stream(self, request):
        self.count += 1
        if self.count % 2 == 0:
            yield ModelCompleted(())
            return
        args = {
            "explanation": "Investigate [red]literal[/red] at NOTE_URL immediately.",
            "plan": [
                {"step": "   Validate STEP_URL before rollout.", "status": "in_progress"},
                {"step": "Verify all remaining outputs carefully before finishing",
                 "status": "pending"},
            ],
        }
        call = ToolCall(new_tool_call_id(), "update_plan", args, raw_arguments=json.dumps(args))
        yield ModelCompleted((ToolCallItem(call, request.items[-1].turn_id, new_step_id()),))
    async def aclose(self):
        self.closed = True

async def main():
    cwd = Path.cwd()
    settings = CorkiSettings(working_directory=cwd, skills_enabled=False, plugins_enabled=False)
    model = Model()
    runtime = await LangGraphRuntime.acreate(
        settings=settings, database_path=cwd / "session.db", model=model
    )
    ui = TerminalUI(settings, cwd / "history", console=TerminalConsole(color_system=None))
    app = CorkiApplication(settings, CorkiPaths.from_home(cwd / "home"), runtime, ui)
    try:
        await app._render_events(runtime.stream("First plan"))
        print("FIXTURE_RESIZE", flush=True)
        await asyncio.to_thread(input)
        await app._render_events(runtime.stream("Second plan"))
    finally:
        await runtime.aclose()
    assert model.closed and model.count == 4
    print("FIXTURE_CLOSED", flush=True)

asyncio.run(main())
""".replace("NOTE_URL", NOTE_URL).replace("STEP_URL", STEP_URL)


@pytest.mark.parametrize("initial_width, resized_width", [(40, 100), (100, 40)])
def test_plan_runtime_output_survives_terminal_resize(tmp_path, initial_width, resized_width):
    child = pexpect.spawn(
        sys.executable,
        ["-c", BOOTSTRAP],
        cwd=str(tmp_path),
        env={
            **os.environ,
            "CORKI_HOME": str(tmp_path / "home"),
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
            "TERM": "xterm-256color",
            "PROMPT_TOOLKIT_NO_CPR": "1",
        },
        dimensions=(30, initial_width),
        encoding="utf-8",
        timeout=20,
    )
    try:
        child.expect_exact("FIXTURE_RESIZE")
        first = child.before
        child.setwinsize(30, resized_width)
        child.sendline("")
        child.expect_exact("FIXTURE_CLOSED")
        second = child.before
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
        for transcript, width in ((first, initial_width), (second, resized_width)):
            clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", transcript)
            assert "Traceback" not in clean
            plan = clean.split("• Updated Plan", 1)[1]
            assert "[red]literal[/red]" in plan
            assert plan.count(NOTE_URL) == 1
            assert plan.count(STEP_URL) == 1
            assert "before rollout." in re.sub(r"\s+", " ", plan)
            assert "□" in plan
            assert "    □    Validate" in plan
            # New output must use the current width; this does not prove old scrollback reflow.
            prose = "Verify all remaining outputs carefully before finishing"
            assert (prose in plan) is (width == 100)
    finally:
        if child.isalive():
            child.close(force=True)


def test_resize_replays_old_plan_and_preserves_composer_draft(tmp_path):
    script = (
        BOOTSTRAP.replace(
            "    try:\n        await app._render_events",
            "    watch = asyncio.create_task(ui.watch_resize())\n"
            "    try:\n        await app._render_events",
        )
        .replace(
            "        await asyncio.to_thread(input)",
            "        submitted = await ui.read_message()\n        assert submitted == 'draft kept'",
        )
        .replace(
            "    finally:\n        await runtime.aclose()",
            "    finally:\n        watch.cancel()\n"
            "        await asyncio.gather(watch, return_exceptions=True)\n"
            "        await runtime.aclose()",
        )
    )
    child = pexpect.spawn(
        sys.executable,
        ["-c", script],
        cwd=str(tmp_path),
        encoding="utf-8",
        timeout=10,
        dimensions=(30, 40),
        env={
            **os.environ,
            "CORKI_HOME": str(tmp_path / "home"),
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
            "TERM": "xterm-256color",
            "PROMPT_TOOLKIT_NO_CPR": "1",
        },
    )
    try:
        child.expect_exact("FIXTURE_RESIZE")
        child.expect_exact("Ask Corki to do anything")
        child.send("draft kept")
        child.expect_exact("draft kept")
        child.setwinsize(30, 100)
        # No new input or model event: the already displayed plan must be rebuilt.
        child.expect_exact("• Updated Plan")
        child.expect_exact(NOTE_URL)
        child.expect_exact("    □    Validate")
        child.expect_exact(STEP_URL)
        child.expect_exact("Verify all remaining outputs carefully before finishing")
        child.send("\r")
        child.expect_exact("FIXTURE_CLOSED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
