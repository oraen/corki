"""Real runtime, execution approval, cancellation and composer on a physical PTY."""

import os
import signal
import sys
import time
from contextlib import suppress
from io import StringIO
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
    class Model:
        requested = False
        closed = False
        async def stream(self, request):
            user = next(i for i in reversed(request.items) if isinstance(i, UserMessageItem))
            step = new_step_id()
            if user.content == "wait":
                yield ModelTextDelta("WAITING_FOR_INTERRUPT\n")
                await asyncio.Future()
            elif user.content == "execute" and not self.requested:
                self.requested = True
                command = "mkdir " + shlex.quote(str(target))
                if sys.argv[2] == "long":
                    code = ("import os,time; from pathlib import Path; "
                            "Path('tool-pid').write_text(str(os.getpid())); "
                            "print('LONG_TOOL_RUNNING',flush=True); time.sleep(300)")
                    command += " && exec " + shlex.quote(sys.executable)
                    command += " -c " + shlex.quote(code)
                call = ToolCall("approved-call", "exec_command", {
                    "cmd": command, "login": False, "yield_time_ms": 30000,
                    "sandbox_permissions": "require_escalated",
                })
                yield ModelCompleted((ToolCallItem(call, user.turn_id, step),))
            else:
                text = "Execution settled." if user.content == "execute" else "Followup complete."
                yield ModelTextDelta(text)
                yield ModelCompleted((AssistantMessageItem(text, user.turn_id, step),))
        async def aclose(self):
            self.closed = True
    class App(CorkiApplication):
        async def _consume_turn(self, message):
            await super()._consume_turn(message)
            if message == "wait":
                assert not self._ui._working_status.active
                assert self._ui._working_status.timer is None
                assert self._input._reader is None
                assert not self._runtime._process_manager.approvals.router._pending
                if sys.argv[2] == "long":
                    # Like Codex, interrupting observation does not destroy a
                    # published terminal session. It stays explicitly owned
                    # until terminal cleanup or runtime shutdown.
                    assert self._runtime._process_manager._sessions
            print("TURN_SETTLED=" + message, flush=True)
    settings = CorkiSettings(cwd, skills_enabled=False, plugins_enabled=False,
        execution_permissions=ExecutionPermissions(
            Path(sys.argv[1]), cwd, '{"type":"read-only"}', approval_policy_json='"on-request"'))
    model = Model()
    runtime = await LangGraphRuntime.acreate(settings=settings, model=model,
        database_path=cwd / "state.db", home_path=cwd / "home")
    (cwd / "thread-id").write_text(str(runtime.thread_id))
    ui = TerminalUI(settings, cwd / "input-history")
    app = App(settings, CorkiPaths.from_home(cwd / "home"), runtime, ui)
    assert await app.run() == 0
    assert target.exists() == (sys.argv[2] != "cancel")
    assert model.closed
    assert not runtime._process_manager._sessions
    assert not runtime._process_manager.approvals.router._pending
    assert not ui._transcript.modal_depth
    assert not ui._working_status.active
    assert ui._working_status.timer is None
    assert app._input._reader is None
    assert not [task for task in asyncio.all_tasks()
                if task is not asyncio.current_task()
                and task.get_name().startswith("corki-realtime-")]
    interruptions = [args for method, args, _ in ui._transcript.calls
                     if method.__name__ == "show_notice" and args == ("Turn interrupted.",)]
    assert len(interruptions) == 1 + (sys.argv[2] != "accept"), interruptions
    assert not list(ui._form_session.history.get_strings())
    assert list(ui._session.history.get_strings()) == ["execute", "wait", "followup"]
    print("LIFECYCLE_VERIFIED", flush=True)
asyncio.run(main())
"""

COLD_PROGRAM = r"""
import asyncio, sys
from pathlib import Path
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.config.permissions import ExecutionPermissions
from corki.core import LangGraphRuntime

async def main():
    cwd = Path.cwd()
    class Model:
        closed = False
        calls = 0
        async def stream(self, request):
            self.calls += 1
            raise AssertionError("cold replay must not sample or reexecute tools")
            yield
        async def aclose(self):
            self.closed = True
    settings = CorkiSettings(cwd, skills_enabled=False, plugins_enabled=False,
        execution_permissions=ExecutionPermissions(
            Path(sys.argv[1]), cwd, '{"type":"read-only"}', approval_policy_json='"on-request"'))
    model = Model()
    runtime = await LangGraphRuntime.acreate(settings=settings, model=model,
        database_path=cwd / "state.db", home_path=cwd / "home",
        thread_id=(cwd / "thread-id").read_text())
    ui = TerminalUI(settings, cwd / "input-history")
    app = CorkiApplication(settings, CorkiPaths.from_home(cwd / "home"), runtime, ui)
    assert await app.run() == 0
    text = ui._transcript.render(100)
    assert "Followup complete." in text
    assert "wait" in text and "execute" in text
    assert model.closed
    assert model.calls == 0
    assert not runtime._process_manager._sessions
    assert not runtime._process_manager.approvals.router._pending
    print("COLD_LIFECYCLE_VERIFIED", flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("decision", ["accept", "cancel", "long"])
@pytest.mark.parametrize("interrupt_key", ["\x03", "\x1b"])
def test_execution_approval_cancel_followup_and_exit(tmp_path, width, decision, interrupt_key):
    compiler = os.environ.get("CORKI_TEST_SANDBOX_COMPILER")
    if sys.platform != "darwin" or not compiler or not Path(compiler).is_file():
        pytest.skip("requires explicit native compiler and macOS")
    import termios

    child = pexpect.spawn(
        sys.executable,
        ["-c", PROGRAM, compiler, decision],
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
    initial_modes = termios.tcgetattr(child.child_fd)
    captured = StringIO()
    child.logfile_read = captured
    try:
        child.expect_exact(f"\x1b]0;{tmp_path.name}\x07")
        child.expect("Ask Corki to do anything")
        child.send("execute")
        time.sleep(0.15)
        child.send("\r")
        child.expect("Yes, proceed once")
        assert not (tmp_path / "approved-operation").exists()
        assert child.expect(["TURN_SETTLED=", pexpect.TIMEOUT], timeout=0.2) == 1
        child.send(interrupt_key if decision == "cancel" else "y")
        if decision == "long":
            # exec_command may buffer output until its yield deadline. Observe the
            # actual child startup, not a command string echoed in approval UI.
            deadline = time.monotonic() + 5
            while not (tmp_path / "tool-pid").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            tool_pid = int((tmp_path / "tool-pid").read_text())
            os.kill(tool_pid, 0)
            child.send(interrupt_key)
        child.expect("TURN_SETTLED=execute" if decision == "accept" else "Turn interrupted")
        assert (tmp_path / "approved-operation").exists() == (decision != "cancel")
        child.expect("Ask Corki to do anything")
        child.send("wait")
        # Cancellation can still be replacing the busy reader. Wait until the
        # new reader has processed the text before the explicit Enter pause.
        child.expect_exact("wait")
        time.sleep(0.15)
        child.send("\r")
        try:
            child.expect("WAITING_FOR_INTERRUPT")
        except (pexpect.TIMEOUT, pexpect.EOF):
            pytest.fail(f"followup after approval did not start: {captured.getvalue()[-5000:]!r}")
        child.send(interrupt_key)
        child.expect("Turn interrupted")
        child.expect("Ask Corki to do anything")
        child.send("followup")
        time.sleep(0.15)
        child.send("\r")
        child.expect("TURN_SETTLED=followup")
        child.expect("Ask Corki to do anything")
        child.sendcontrol("t")
        child.expect_exact("\x1b[?1049h")
        child.expect("Followup complete")
        child.sendcontrol("c")
        child.expect_exact("\x1b[?1049l")
        child.sendcontrol("d")
        child.expect_exact("\x1b]0;\x07")
        child.expect("LIFECYCLE_VERIFIED")
        child.expect(pexpect.EOF)
        restored_modes = termios.tcgetattr(child.child_fd)
        assert restored_modes[0] & termios.ICRNL == initial_modes[0] & termios.ICRNL
        assert restored_modes[3] & termios.ICANON == initial_modes[3] & termios.ICANON
        child.close()
        assert child.exitstatus == 0
        if decision == "long":
            with pytest.raises(ProcessLookupError):
                os.kill(tool_pid, 0)
    finally:
        if decision == "long" and (pid_file := tmp_path / "tool-pid").exists():
            # A failed assertion may force-close the host before its normal
            # runtime shutdown. Do not leave this fixture's sleeping child alive.
            with suppress(ProcessLookupError):
                os.kill(int(pid_file.read_text()), signal.SIGTERM)
        if child.isalive():
            child.close(force=True)
    # A separate interpreter, same persisted thread: viewing history must not run tools again.
    cold = pexpect.spawn(
        sys.executable,
        ["-c", COLD_PROGRAM, compiler],
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
        cold.expect("Followup complete")
        cold.expect("Ask Corki to do anything")
        cold.send("/status")
        time.sleep(0.15)
        cold.send("\r")
        cold.expect("Directory:")
        cold.expect("Ask Corki to do anything")
        cold.sendcontrol("d")
        cold.expect("COLD_LIFECYCLE_VERIFIED")
        cold.expect(pexpect.EOF)
        cold.close()
        assert cold.exitstatus == 0
        assert (tmp_path / "approved-operation").exists() == (decision != "cancel")
    finally:
        if cold.isalive():
            cold.close(force=True)
