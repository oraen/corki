"""A real execution approval waits for the model menu's input ownership."""

import os
import sys
import time
from pathlib import Path

import pexpect
import pytest

PROGRAM = r"""
import asyncio, shlex, sys
from pathlib import Path
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.config.permissions import ExecutionPermissions
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelTextDelta
from corki.protocol.items import AssistantMessageItem, ToolCallItem, UserMessageItem, new_step_id
from corki.protocol.tools import ToolCall

async def main():
    cwd = Path.cwd()
    target = cwd / "approved-operation"
    menu_open = asyncio.Event()
    class Model:
        requested = False
        closed = False
        async def stream(self, request):
            user = next(i for i in reversed(request.items) if isinstance(i, UserMessageItem))
            step = new_step_id()
            if not self.requested:
                self.requested = True
                yield ModelTextDelta("WAITING_FOR_INPUT\n")
                await menu_open.wait()
                if sys.argv[2] == "overlay":
                    await asyncio.sleep(1.2)
                call = ToolCall("menu-approval", "exec_command", {
                    "cmd": "mkdir " + shlex.quote(str(target)), "login": False,
                    "sandbox_permissions": "require_escalated",
                })
                yield ModelCompleted((ToolCallItem(call, user.turn_id, step),))
            else:
                yield ModelCompleted((AssistantMessageItem("Done.", user.turn_id, step),))
        async def aclose(self):
            self.closed = True
    class UI(TerminalUI):
        def __init__(self, *args):
            super().__init__(*args)
            def edited(buffer):
                if sys.argv[2] == "typing" and buffer.text == "draft":
                    menu_open.set()
            self._session.default_buffer.on_text_changed += edited
        async def read_model(self, current):
            menu_open.set()
            return await super().read_model(current)
        async def read_elicitation(self, request):
            assert not target.exists()
            print("APPROVAL_OWNS_INPUT", flush=True)
            return await super().read_elicitation(request)
    class App(CorkiApplication):
        async def _handle_elicitation(self, request):
            print("APPROVAL_PENDING", flush=True)
            await super()._handle_elicitation(request)
        async def _consume_turn(self, message):
            await super()._consume_turn(message)
            print("TURN_SETTLED", flush=True)
    settings = CorkiSettings(cwd, skills_enabled=False, plugins_enabled=False,
        execution_permissions=ExecutionPermissions(
            Path(sys.argv[1]), cwd, '{"type":"read-only"}', approval_policy_json='"on-request"'))
    model = Model()
    runtime = await LangGraphRuntime.acreate(settings=settings, model=model,
        database_path=cwd / "state.db", home_path=cwd / "home")
    ui = UI(settings, cwd / "input-history")
    app = App(settings, CorkiPaths.from_home(cwd / "home"), runtime, ui)
    assert await app.run() == 0
    assert target.is_dir()
    assert model.closed
    assert not runtime._process_manager._sessions
    assert not runtime._process_manager.approvals.router._pending
    assert not ui._transcript.modal_depth
    assert not app._input._modals and not app._input._lock.locked()
    assert not list(ui._form_session.history.get_strings())
    expected_history = ["execute", "drafty" if sys.argv[2] == "typing" else "/model"]
    assert list(ui._session.history.get_strings()) == expected_history
    expected = "Local/Chosen" if sys.argv[2] in {"confirm", "overlay"} else settings.model
    assert runtime.thread_settings.model == expected
    print("OWNERSHIP_VERIFIED", flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("menu_action", ["confirm", "cancel", "typing", "overlay"])
def test_model_menu_does_not_accept_later_execution_approval(tmp_path, width, menu_action):
    compiler = os.environ.get("CORKI_TEST_SANDBOX_COMPILER")
    if sys.platform != "darwin" or not compiler or not Path(compiler).is_file():
        pytest.skip("requires explicit native compiler and macOS")
    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM, compiler, menu_action],
        cwd=tmp_path,
        encoding="utf-8",
        timeout=20,
        dimensions=(30, width),
        env={
            **os.environ,
            "CORKI_HOME": str(tmp_path / "home"),
            "TERM": "xterm-256color",
            "PROMPT_TOOLKIT_NO_CPR": "1",
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
        },
    )
    try:
        child.expect("Ask Corki to do anything")
        child.send("execute")
        time.sleep(0.15)
        child.send("\r")
        child.expect("WAITING_FOR_INPUT")
        child.send("draft" if menu_action == "typing" else "/model")
        if menu_action != "typing":
            time.sleep(0.15)
            child.send("\r")
        if menu_action == "overlay":
            child.expect("Select model")
            child.send("Local/Chosen")
        child.expect("APPROVAL_PENDING")
        if menu_action != "overlay":
            assert child.expect(["APPROVAL_OWNS_INPUT", pexpect.TIMEOUT], timeout=0.3) == 1
        assert not (tmp_path / "approved-operation").exists()
        if menu_action == "typing":
            child.send("y")
            assert child.expect(["APPROVAL_OWNS_INPUT", pexpect.TIMEOUT], timeout=0.3) == 1
        elif menu_action != "overlay":
            child.send("Local/Chosen\r" if menu_action == "confirm" else "\x1b")
        child.expect("APPROVAL_OWNS_INPUT")
        child.expect("Yes, proceed once")
        # Menu Enter/Esc must not become the newly displayed approval's answer.
        assert child.expect(["TURN_SETTLED", pexpect.TIMEOUT], timeout=0.3) == 1
        assert not (tmp_path / "approved-operation").exists()
        child.send("y")
        if menu_action == "overlay":
            child.expect("Select model")
            child.expect("Local/Chosen")
            child.send("\r")
        child.expect("TURN_SETTLED")
        assert (tmp_path / "approved-operation").is_dir()
        child.expect("drafty" if menu_action == "typing" else "Ask Corki to do anything")
        if menu_action == "typing":
            child.send("\r")
            child.expect("TURN_SETTLED")
            child.expect("Ask Corki to do anything")
        child.sendcontrol("d")
        child.expect("OWNERSHIP_VERIFIED")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
    finally:
        if child.isalive():
            child.close(force=True)
