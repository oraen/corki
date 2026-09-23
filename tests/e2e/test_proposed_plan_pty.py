"""Final plan source reflows in a real PTY while the composer keeps its draft."""

import os
import sys

import pexpect
import pytest

BOOTSTRAP = r"""
import asyncio
from pathlib import Path
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.cli.terminal_console import TerminalConsole
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelTextDelta
from corki.protocol.items import AssistantMessageItem, new_step_id

class Model:
    count = 0
    closed = False
    async def stream(self, request):
        self.count += 1
        text = ("<proposed_plan>\nInspect the current files and verify all remaining "
                "behavior before changing ownership.\n</proposed_plan>")
        item = AssistantMessageItem(text, request.items[-1].turn_id, new_step_id())
        if STREAM_DRAFT:
            yield ModelTextDelta(text, item.id)
            print("PLAN_WAITING", flush=True)
            await asyncio.to_thread(input)
        yield ModelCompleted((item,))
    async def aclose(self):
        self.closed = True

async def main():
    cwd = Path.cwd()
    settings = CorkiSettings(working_directory=cwd, skills_enabled=False,
                            plugins_enabled=False, collaboration_mode="plan")
    model = Model()
    runtime = await LangGraphRuntime.acreate(settings=settings, model=model,
                    database_path=cwd / "session.db", home_path=cwd / "home")
    ui = TerminalUI(settings, cwd / "history", console=TerminalConsole(color_system=None))
    app = CorkiApplication(settings, CorkiPaths.from_home(cwd / "home"), runtime, ui)
    watch = asyncio.create_task(ui.watch_resize())
    try:
        await app._render_events(runtime.stream("Make a plan"))
        print("READY_RESIZE", flush=True)
        assert await ui.read_message() == "draft kept"
        assert model.count == 1
    finally:
        watch.cancel()
        await asyncio.gather(watch, return_exceptions=True)
        await runtime.aclose()
    assert model.closed
    print("CLOSED_PLAN", flush=True)

asyncio.run(main())
"""


@pytest.mark.parametrize("initial_width, target_width", [(40, 100), (100, 40)])
@pytest.mark.parametrize("streamed", [False, True])
def test_completed_plan_resize_keeps_composer_draft(
    tmp_path, initial_width, target_width, streamed
):
    child = pexpect.spawn(
        sys.executable,
        ["-c", BOOTSTRAP.replace("STREAM_DRAFT", repr(streamed))],
        cwd=str(tmp_path),
        encoding="utf-8",
        timeout=20,
        dimensions=(30, initial_width),
        env={
            **os.environ,
            "CORKI_HOME": str(tmp_path / "home"),
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
            "TERM": "xterm-256color",
            "PROMPT_TOOLKIT_NO_CPR": "1",
        },
    )
    try:
        if streamed:
            child.expect_exact("PLAN_WAITING")
            child.expect_exact("• Proposed Plan")
            child.expect_exact("changing ownership.")
            child.sendline("")
        child.expect_exact("READY_RESIZE")
        assert "• Proposed Plan" in child.before
        assert "<proposed_plan>" not in child.before
        child.expect_exact("Ask Corki to do anything")
        child.send("draft kept")
        child.expect_exact("draft kept")
        child.setwinsize(30, target_width)
        child.expect_exact("• Proposed Plan")
        child.expect_exact("changing ownership.")
        child.sendline("")
        child.expect_exact("CLOSED_PLAN")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
