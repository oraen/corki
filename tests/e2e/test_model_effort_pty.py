"""Model and effort are published together only after the final confirmation."""

import os
import sys

import pexpect
import pytest

PROGRAM = r"""
import asyncio, sys
from pathlib import Path
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiSettings, CorkiPaths
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.items import AssistantMessageItem, new_step_id

async def main():
    cwd = Path.cwd()
    settings = CorkiSettings(cwd, model="old", reasoning_effort="low",
        skills_enabled=False, plugins_enabled=False, realtime_enabled=False,
        model_contexts=(ModelContextInfo("old", 20000, supported_reasoning_levels=("low",)),
                        ModelContextInfo("new", 20000,
                            supported_reasoning_levels=("low", "high", "max"))))
    class Model:
        requests = []
        async def stream(self, request):
            self.requests.append((request.model, request.reasoning_effort))
            yield ModelCompleted((AssistantMessageItem(
                "DONE", request.items[-1].turn_id, new_step_id()),))
        async def aclose(self): pass
    model = Model()
    runtime = await LangGraphRuntime.acreate(settings=settings, model=model,
        database_path=cwd / "session.db", home_path=cwd / "home")
    changes = []
    original = runtime.update_thread_settings
    async def update(**kwargs):
        changes.append(kwargs)
        return await original(**kwargs)
    runtime.update_thread_settings = update
    ui = TerminalUI(settings, cwd / "history")
    class Application(CorkiApplication):
        async def _consume_turn(self, message):
            await super()._consume_turn(message)
            print("TURN_SETTLED", flush=True)
    app = Application(settings, CorkiPaths.from_home(cwd / "home"), runtime, ui)
    assert await app.run() == 0
    expected = sys.argv[1]
    assert changes == [{"model": "new", "reasoning_effort": expected}]
    assert model.requests == [("new", expected)]
    assert ui._transcript.modal_depth == 0
    print("EFFORT_VERIFIED", flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("effort", ["high", "max"])
def test_model_effort_confirmation_is_atomic(tmp_path, width, effort):
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM, effort],
        cwd=tmp_path,
        env={**os.environ, "TERM": "xterm-256color", "PROMPT_TOOLKIT_NO_CPR": "1"},
        encoding="utf-8",
        timeout=15,
        dimensions=(30, width),
    )
    try:
        child.expect("Ask Corki to do anything")
        # Cancel at the second stage: even the chosen model must remain unpublished.
        child.send("/model\r")
        child.expect_exact("Select model")
        child.send("new\r")
        child.expect_exact("Select reasoning effort")
        child.send("\x1b")
        child.expect("Ask Corki to do anything")
        child.send("/model\r")
        child.expect_exact("Select model")
        child.send("new\r")
        child.expect_exact("Select reasoning effort")
        child.send(effort + "\r")
        if effort == "max":
            child.expect_exact("Confirm reasoning effort")
            child.send("\x1b[B\r")
        child.expect_exact("Model: new.")
        child.expect("Ask Corki to do anything")
        child.send("/status\r")
        child.expect_exact("Reasoning:")
        child.expect_exact(effort)
        child.expect("Ask Corki to do anything")
        child.send("hello\r")
        child.expect_exact("DONE")
        child.expect_exact("TURN_SETTLED")
        child.expect("Ask Corki to do anything")
        child.sendcontrol("d")
        child.expect_exact("EFFORT_VERIFIED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
