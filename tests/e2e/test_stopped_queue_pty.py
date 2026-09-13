"""Stopping work restores queued text and requires a fresh keyboard submission."""

import os
import sys

import pexpect
import pytest

PROGRAM = r"""
import asyncio, sys
from pathlib import Path
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelTextDelta
from corki.protocol.items import AssistantMessageItem, ContextItem, UserMessageItem, new_step_id

async def main():
    mode = sys.argv[1]
    class Model:
        requests = []
        closed = False
        async def stream(self, request):
            assert any(isinstance(i, ContextItem) and i.key == "mode.plan"
                       for i in request.items) == (mode == "plan")
            user = next(i for i in reversed(request.items) if isinstance(i, UserMessageItem))
            self.requests.append(user.content)
            if len(self.requests) == 1:
                yield ModelTextDelta("Working\n")
                print("WAITING_FOR_STOP", flush=True)
                await asyncio.Future()
            yield ModelCompleted((AssistantMessageItem("Finished restored input",
                user.turn_id, new_step_id()),))
        async def aclose(self):
            self.closed = True
    class UI(TerminalUI):
        def set_collaboration_mode(self, mode):
            raise AssertionError("active Shift+Tab must not publish a mode change")
        def restore_queued_inputs(self, messages):
            super().restore_queued_inputs(messages)
            assert self._draft == "queued one\nqueued two"
            assert model.requests == ["initial"]
            print("QUEUE_RESTORED", flush=True)
    class Application(CorkiApplication):
        async def _consume_turn(self, message):
            await super()._consume_turn(message)
            if message != "initial":
                print("RESTORED_TURN_FINISHED", flush=True)
    cwd = Path.cwd()
    settings = CorkiSettings(cwd, skills_enabled=False, plugins_enabled=False,
                            collaboration_mode=mode)
    model = Model()
    runtime = LangGraphRuntime.create(settings=settings, database_path=cwd / "sessions.db",
                                     model=model, home_path=cwd / "home")
    app = Application(settings, CorkiPaths.from_home(cwd / "home"), runtime,
                      UI(settings, cwd / "input-history"))
    assert await app.run() == 0
    assert model.closed and model.requests == ["initial", "queued one\nqueued two"]
    assert runtime.thread_settings.collaboration_mode == mode
    print("STOP_QUEUE_VERIFIED", flush=True)

asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("keyboard", [False, True])
@pytest.mark.parametrize("pending_steers", [False, True])
@pytest.mark.parametrize("shift_tab", [False, True])
@pytest.mark.parametrize("mode", ["default", "plan"])
def test_stop_restores_queue_until_user_submits_again(
    tmp_path, width, keyboard, pending_steers, shift_tab, mode
):
    program = PROGRAM
    if pending_steers:
        program = program.replace(
            '"queued one\\nqueued two"',
            '"pending steer\\npending steer\\nqueued one\\nqueued two"',
        )
    child = pexpect.spawn(
        sys.executable,
        ["-c", program, mode],
        cwd=tmp_path,
        encoding="utf-8",
        timeout=15,
        dimensions=(30, width),
        env={
            **os.environ,
            "CORKI_HOME": str(tmp_path / "home"),
            "TERM": "xterm-256color",
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
            "PROMPT_TOOLKIT_NO_CPR": "1",
        },
    )
    try:
        child.expect("Ask Corki to do anything")
        child.send("initial\r")
        child.expect_exact("WAITING_FOR_STOP")
        if pending_steers:
            for _ in range(2):
                child.send("pending steer\r")
                child.expect("Ask Corki to do anything")
        for message in ("queued one", "queued two"):
            child.send(message + ("\x1b[Z" if shift_tab else "") + "\t")
            child.expect_exact("Queued for the next turn.")
        if pending_steers:
            # Enter must not replace active work or consume the existing queue.
            child.send("/compact\r")
            child.expect_exact("disabled while")
        child.sendcontrol("c") if keyboard else child.send("/stop\r")
        child.expect_exact("QUEUE_RESTORED")
        child.expect_exact("queued two")
        child.send("\r")
        child.expect_exact("RESTORED_TURN_FINISHED")
        child.expect("Ask Corki to do anything")
        child.sendcontrol("d")
        child.expect_exact("STOP_QUEUE_VERIFIED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
