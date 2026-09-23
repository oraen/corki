import asyncio
import json
import os
import re
import shlex
import signal
import subprocess
import sys
from contextlib import suppress

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools.builtin.process import ProcessManager

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX owned process-group fixture")

SCRIPT = """
import os, signal, time
r, w = os.pipe()
pid = os.fork()
if pid == 0:
    os.close(r)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    os.write(w, b'1')
    os.close(w)
    time.sleep(15)
    os._exit(0)
os.close(w)
os.read(r, 1)
os.close(r)
print('PARENT-OUTPUT', flush=True)
print(f'CHILD:{pid}', flush=True)
"""


@pytest.mark.parametrize("tty", [False, True])
@pytest.mark.parametrize("action", ["observe", "close"])
@pytest.mark.parametrize("delayed_leader", [False, True])
def test_exited_leader_with_inherited_stdout_does_not_wedge_manager(
    tmp_path, tty, action, delayed_leader
):
    async def scenario():
        manager = ProcessManager()
        script = (
            SCRIPT.replace("print('PARENT-OUTPUT'", "time.sleep(0.15)\nprint('PARENT-OUTPUT'")
            if delayed_leader
            else SCRIPT
        )
        command = "exec " + shlex.join([sys.executable, "-c", script])
        task = asyncio.create_task(
            manager.execute(
                command, cwd=tmp_path, yield_seconds=0.05, timeout_seconds=10, tty=tty, login=False
            )
        )
        sessions = ()
        closing = None
        try:
            async with asyncio.timeout(2):
                while not manager._sessions:
                    await asyncio.sleep(0.005)
                sessions = tuple(manager._sessions.values())
                while sessions[0].process.returncode is None:
                    await asyncio.sleep(0.005)
            if action == "close":
                closing = asyncio.create_task(manager.terminate_all())
                await asyncio.wait_for(asyncio.shield(closing), 1.5)
                await asyncio.gather(task, return_exceptions=True)
            else:
                async with asyncio.timeout(1.5):
                    result = await asyncio.shield(task)
                    output = result.output
                    if result.session_id is not None:
                        # execute may have yielded before the leader exited.
                        # That immutable observation is not refreshed by awaiting
                        # its already-completed task; observe the same session.
                        result = await manager.write_stdin(result.session_id, "", yield_seconds=0)
                        output += result.output
                assert result.exit_code == 0 and result.session_id is None
                assert "PARENT-OUTPUT" in output and not result.timed_out
            assert not manager._sessions
            assert all(s.reader_task.done() and s.timeout_task.done() for s in sessions)
            assert all(s.pty_master_fd is None for s in sessions)
        finally:
            # Only fixture groups captured from this manager; never broad PID matching.
            for session in sessions:
                cleanup = getattr(session, "cleanup_task", None)
                if cleanup is not None and cleanup.done() and cleanup.exception() is None:
                    continue
                with suppress(ProcessLookupError):
                    os.killpg(session.process.pid, signal.SIGKILL)
            task.cancel()
            if closing is not None:
                closing.cancel()
                await asyncio.gather(closing, return_exceptions=True)
            await asyncio.gather(task, return_exceptions=True)
            await manager.terminate_all()

    asyncio.run(scenario())


@pytest.mark.parametrize("tty", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_inherited_stdout_completes_real_runtime_and_cold_history(tmp_path, tty, nested):
    async def scenario():
        requests, sessions, children = [], [], []
        command = "exec " + shlex.join([sys.executable, "-c", SCRIPT])

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    args = {"cmd": command, "login": False, "tty": tty, "yield_time_ms": 1000}
                    call = (
                        ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments=(
                                f"const r = await tools.exec_command({json.dumps(args)}); "
                                "text(r.exit_code); text(r.output);"
                            ),
                        )
                        if nested
                        else ToolCall(new_tool_call_id(), "exec_command", args)
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert "PARENT-OUTPUT" in result.content and not result.is_error
                    children.append(int(re.search(r"CHILD:(\d+)", result.content)[1]))
                    assert (
                        "Script completed" if nested else "Process exited with code 0"
                    ) in result.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            tool_mode="code_mode" if nested else "direct",
        )

        async def create(thread=None):
            runtime = await LangGraphRuntime.acreate(
                settings=settings,
                database_path=tmp_path / "runtime.db",
                model=Model(),
                thread_id=thread,
            )
            original = runtime._process_manager._start_session

            async def capture(*args, **kwargs):
                session = await original(*args, **kwargs)
                sessions.append(session)
                return session

            runtime._process_manager._start_session = capture
            return runtime

        runtime = await create()
        thread = runtime.thread_id
        try:
            async with asyncio.timeout(3):
                events = [e async for e in runtime.stream("run")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert sessions and all(s.cleanup_task.done() for s in sessions)
            assert not runtime._process_manager._sessions
            # A joined/cancelled reader alone cannot prove descendants were stopped.
            async with asyncio.timeout(1):
                while True:
                    status = subprocess.run(
                        ["ps", "-o", "stat=", "-p", str(children[0])],
                        capture_output=True,
                        text=True,
                        timeout=1,
                        check=False,
                    ).stdout.strip()
                    if not status or status.startswith("Z"):
                        break
                    await asyncio.sleep(0.005)
        finally:
            for session in sessions:
                if session.cleanup_task is None or not session.cleanup_task.done():
                    with suppress(ProcessLookupError):
                        os.killpg(session.process.pid, signal.SIGKILL)
            await runtime.aclose()
        cold = await create(thread)
        try:
            events = [e async for e in cold.stream("continue")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 3
        finally:
            await cold.aclose()

    asyncio.run(scenario())
