import asyncio
import errno
import json
import os
import re
import shlex
import signal
import sys
from contextlib import suppress
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools.builtin import pty_spawn
from corki.tools.builtin.process import ProcessManager
from corki.tools.builtin.pty_spawn import spawn_pty

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX stdin/controlling terminal")


def command(script):
    return "exec " + shlex.join([sys.executable, "-c", script])


async def observe_until(manager, result, *, ready=None):
    """Poll the same owned session; an observation yield is not a completion deadline."""
    output = result.output
    async with asyncio.timeout(3):
        while result.exit_code is None and (ready is None or ready not in output):
            assert result.session_id is not None
            result = await manager.write_stdin(result.session_id, "", yield_seconds=0.05)
            output += result.output
    return replace(result, output=output)


@pytest.mark.parametrize("gated", [False, True])
@pytest.mark.parametrize("case", ["eof", "closed", "raw", "controlling"])
def test_source_stdin_contract(tmp_path, case, gated):
    async def scenario():
        manager = ProcessManager()
        scripts = {
            "eof": "import sys; print('EOF:' + repr(sys.stdin.read()))",
            "closed": "import time; time.sleep(20)",
            "raw": (
                "import os,tty; tty.setraw(0); print('READY',flush=True); "
                "print('BYTE:'+os.read(0,1).hex())"
            ),
            "controlling": "import os; print('FOREGROUND:' + str(os.tcgetpgrp(0)==os.getpgrp()))",
        }
        gate = tmp_path / "release"
        script = scripts[case]
        if gated:
            script = (
                "import pathlib,time\n"
                f"while not pathlib.Path({str(gate)!r}).exists(): time.sleep(.01)\n" + script
            )
        try:
            result = await manager.execute(
                command(script),
                cwd=tmp_path,
                login=False,
                tty=case in ("raw", "controlling"),
                yield_seconds=0.2,
                timeout_seconds=3,
            )
            if gated:
                assert result.exit_code is None and result.session_id is not None
                assert result.output == ""
                gate.touch()
            if case == "closed":
                with pytest.raises(ValueError, match="stdin is closed.*tty=true"):
                    await manager.write_stdin(result.session_id, "data\n", yield_seconds=0)
                assert result.session_id in manager._sessions
            elif case == "raw":
                result = await observe_until(manager, result, ready="READY")
                assert "READY" in result.output
                result = await manager.write_stdin(result.session_id, "\x03", yield_seconds=1)
                result = await observe_until(manager, result)
                assert result.exit_code == 0 and "BYTE:03" in result.output
            else:
                result = await observe_until(manager, result)
                expected = "EOF:''" if case == "eof" else "FOREGROUND:True"
                assert result.exit_code == 0 and expected in result.output
        finally:
            await manager.terminate_all()

    asyncio.run(scenario())


@pytest.mark.parametrize("gated", [False, True])
@pytest.mark.parametrize("tty", [False, True])
def test_interrupt_uses_pipe_group_or_pty_foreground_group(tmp_path, tty, gated):
    async def scenario():
        manager = ProcessManager()
        child_pid = parent_pid = None
        script = (
            "import signal,time; signal.signal(signal.SIGINT, lambda *a: exit(0)); "
            "print('READY',flush=True); time.sleep(20)"
        )
        if tty:
            script = """
import os, signal
r,w=os.pipe()
child=os.fork()
if child==0:
    os.close(r)
    os.setpgid(0,0)
    def interrupted(*args):
        print('CHILD-INTERRUPTED',flush=True)
        os._exit(0)
    signal.signal(signal.SIGINT,interrupted)
    os.write(w,b'1')
    os.close(w)
    signal.pause()
    os._exit(2)
os.close(w)
os.read(r,1)
os.close(r)
os.tcsetpgrp(0,child)
print('READY CHILD:'+str(child),flush=True)
_,status=os.waitpid(child,0)
print('PARENT-SURVIVED:'+str(os.waitstatus_to_exitcode(status)),flush=True)
"""
        gate = tmp_path / "release"
        if gated:
            script = (
                "import pathlib,time\n"
                f"while not pathlib.Path({str(gate)!r}).exists(): time.sleep(.01)\n" + script
            )
        try:
            initial = await manager.execute(
                command(script),
                cwd=tmp_path,
                tty=tty,
                login=False,
                yield_seconds=0.2,
                timeout_seconds=3,
            )
            if gated:
                assert initial.exit_code is None and initial.session_id is not None
                assert initial.output == ""
                gate.touch()
            initial = await observe_until(manager, initial, ready="READY")
            assert "READY" in initial.output
            parent_pid = manager._sessions[initial.session_id].process.pid
            if tty:
                child_pid = int(re.search(r"CHILD:(\d+)", initial.output)[1])
            result = await manager.write_stdin(initial.session_id, "\x03", yield_seconds=1)
            result = await observe_until(manager, result)
            assert result.exit_code == 0
            if tty:
                assert "CHILD-INTERRUPTED" in result.output
                assert "PARENT-SURVIVED:0" in result.output
        finally:
            if child_pid is not None:
                with suppress(ProcessLookupError):
                    if os.getsid(child_pid) == parent_pid and os.getpgid(child_pid) == child_pid:
                        os.kill(child_pid, signal.SIGKILL)
            await manager.terminate_all()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["setup", "exec"])
def test_pty_startup_reports_and_reaps_helper_failure(tmp_path, failure):
    import pty

    async def scenario():
        manager = ProcessManager()
        stopped = []
        master, slave = pty.openpty()
        null = os.open(os.devnull, os.O_RDWR)

        async def terminate(process):
            stopped.append(process)
            await manager._terminate(process)

        try:
            with pytest.raises(OSError) as error:
                await spawn_pty(
                    ["/definitely-missing-corki-pty-executable"],
                    cwd=tmp_path,
                    slave_fd=null if failure == "setup" else slave,
                    terminate=terminate,
                )
            assert error.value.errno in (
                (errno.ENOTTY, errno.ENODEV) if failure == "setup" else (errno.ENOENT,)
            )
            assert len(stopped) == 1 and stopped[0].returncode is not None
        finally:
            os.close(master)
            os.close(slave)
            os.close(null)

    asyncio.run(scenario())


def test_cancelled_pty_handshake_reaps_owned_helper(tmp_path, monkeypatch):
    import pty

    async def scenario():
        master, slave = pty.openpty()
        entered = asyncio.Event()
        stopped = []

        async def ready(fd):
            entered.set()
            await asyncio.Event().wait()

        async def terminate(process):
            stopped.append(process)
            await ProcessManager._terminate(process)

        monkeypatch.setattr(pty_spawn, "_ready", ready)
        task = asyncio.create_task(
            spawn_pty(
                ["/bin/sleep", "20"],
                cwd=tmp_path,
                slave_fd=slave,
                terminate=terminate,
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            assert len(stopped) == 1 and stopped[0].returncode is not None
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            os.close(master)
            os.close(slave)

    asyncio.run(scenario())


def test_python_helper_does_not_leave_sigpipe_ignored(tmp_path):
    async def scenario():
        manager = ProcessManager()
        try:
            result = await manager.execute(
                "kill -PIPE $$; printf SURVIVED",
                cwd=tmp_path,
                tty=True,
                login=False,
                yield_seconds=1,
                timeout_seconds=3,
            )
            assert result.exit_code not in (None, 0) and "SURVIVED" not in result.output
        finally:
            await manager.terminate_all()

    asyncio.run(scenario())


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("case", ["eof", "closed", "raw"])
def test_runtime_stdin_contract_and_cold_no_replay(tmp_path, nested, case):
    async def scenario():
        requests, effects = [], []
        scripts = {
            "eof": "import sys; print('EOF:'+repr(sys.stdin.read()))",
            "closed": "import time; print('READY',flush=True); time.sleep(20)",
            "raw": (
                "import os,tty; tty.setraw(0); print('READY',flush=True); "
                "print('BYTE:'+os.read(0,1).hex())"
            ),
        }
        steps = 1 if case == "eof" else 2

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) <= steps:
                    if len(requests) == 1:
                        name, args = (
                            "exec_command",
                            {
                                "cmd": command(scripts[case]),
                                "login": False,
                                "tty": case == "raw",
                                "yield_time_ms": 1000,
                            },
                        )
                    else:
                        result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                        assert "READY" in result.content and not result.is_error
                        session = re.search(
                            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", result.content
                        )[0]
                        name, args = (
                            "write_stdin",
                            {
                                "session_id": session,
                                "chars": "\x03" if case == "raw" else "input",
                                "yield_time_ms": 0,
                            },
                        )
                    call = (
                        ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments=f"text(await tools.{name}({json.dumps(args)}));",
                        )
                        if nested
                        else ToolCall(new_tool_call_id(), name, args)
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    expected = {"eof": "EOF:''", "closed": "stdin is closed", "raw": "BYTE:03"}[
                        case
                    ]
                    assert expected in result.content
                    assert result.is_error == (case == "closed")
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            tool_mode="code_mode" if nested else "direct",
        )

        def create(thread=None):
            runtime = LangGraphRuntime.create(
                settings=settings,
                database_path=tmp_path / "stdin.db",
                model=Model(),
                thread_id=thread,
            )
            manager = runtime._process_manager
            start, write = manager._start_session, manager._write_stdin

            async def counted_start(*args, **kwargs):
                effects.append("spawn")
                return await start(*args, **kwargs)

            async def counted_write(*args, **kwargs):
                effects.append("input")
                return await write(*args, **kwargs)

            manager._start_session, manager._write_stdin = counted_start, counted_write
            return runtime

        runtime = create()
        try:
            events = [e async for e in runtime.stream("run")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            if case == "closed":
                session = next(iter(runtime._process_manager._sessions.values()))
                assert session.process.returncode is None and session.process.stdin is None
            else:
                assert not runtime._process_manager._sessions
        finally:
            await runtime.aclose()
        cold = create(runtime.thread_id)
        try:
            events = [e async for e in cold.stream("continue")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == steps + 2
            assert effects == (["spawn"] if case == "eof" else ["spawn", "input"])
        finally:
            await cold.aclose()

    asyncio.run(scenario())
