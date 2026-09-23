import asyncio
import json
import os
import re
import shlex
import signal
import sqlite3
import sys

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools.builtin.process import ProcessManager

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX signal statuses")


@pytest.mark.parametrize("tty", [False, True])
@pytest.mark.parametrize("sent", [signal.SIGHUP, signal.SIGTERM, signal.SIGKILL])
def test_default_backend_signal_projection_preserves_raw_process_state(tmp_path, tty, sent):
    async def scenario():
        manager = ProcessManager()
        sessions = []
        start = manager._start_session

        async def capture(*args, **kwargs):
            session = await start(*args, **kwargs)
            sessions.append(session)
            return session

        manager._start_session = capture
        script = f"import os; print('MARKER',flush=True); os.kill(os.getpid(),{int(sent)})"
        command = "exec " + shlex.join([sys.executable, "-c", script])
        try:
            result = await manager.execute(
                command, cwd=tmp_path, login=False, tty=tty, yield_seconds=1
            )
            assert result.exit_code == (1 if tty else 128 + sent)
            assert "MARKER" in result.output and result.session_id is None
            assert sessions[0].process.returncode == -sent
            assert sessions[0].cleanup_task.done() and not manager._sessions
        finally:
            await manager.terminate_all()

    asyncio.run(scenario())


@pytest.mark.parametrize("tty", [False, True])
@pytest.mark.parametrize("code", [0, 7])
def test_real_normal_exit_is_unchanged(tmp_path, tty, code):
    async def scenario():
        manager = ProcessManager()
        try:
            result = await manager.execute(
                f"exit {code}", cwd=tmp_path, login=False, tty=tty, yield_seconds=1
            )
            assert result.exit_code == code and result.session_id is None
        finally:
            await manager.terminate_all()

    asyncio.run(scenario())


@pytest.mark.parametrize("tty", [False, True])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("poll", [False, True])
@pytest.mark.parametrize("sent", [signal.SIGTERM, signal.SIGKILL])
def test_runtime_signal_status_ledger_and_cold_history(tmp_path, tty, nested, poll, sent):
    async def scenario():
        requests, children = [], []
        expected = 1 if tty else 128 + sent
        gate = tmp_path / "release"
        script = f"""
import os, time
from pathlib import Path
print('READY',flush=True)
if {poll!r}:
    while not Path({str(gate)!r}).exists():
        time.sleep(.005)
print('SIGNAL',flush=True)
os.kill(os.getpid(),{int(sent)})
"""
        command = "exec " + shlex.join([sys.executable, "-c", script])
        steps = 2 if poll else 1

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) <= steps:
                    if len(requests) == 1:
                        name, args = (
                            "exec_command",
                            {
                                "cmd": command,
                                "login": False,
                                "tty": tty,
                                "yield_time_ms": 0 if poll else 1000,
                            },
                        )
                    else:
                        result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                        # A minimum-yield call can return before Python/PTY startup
                        # prints READY. The live session, not early output, is the
                        # readiness contract; the child observes the release file.
                        assert not result.is_error
                        session = re.search(
                            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", result.content
                        )[0]
                        gate.touch()
                        name, args = "write_stdin", {"session_id": session, "yield_time_ms": 0}
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
                    marker = (
                        f'"exit_code":{expected}'
                        if nested
                        else f"Process exited with code {expected}"
                    )
                    assert marker in result.content and "SIGNAL" in result.content
                    assert not result.is_error  # Command exit is not failed dispatch.
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            tool_mode="code_mode" if nested else "direct",
        )
        database = tmp_path / "status.db"

        async def create(thread=None):
            runtime = await LangGraphRuntime.acreate(
                settings=settings, database_path=database, model=Model(), thread_id=thread
            )
            manager = runtime._process_manager
            start = manager._start_session

            async def capture(*args, **kwargs):
                session = await start(*args, **kwargs)
                children.append(session)
                return session

            manager._start_session = capture
            return runtime

        runtime = await create()
        try:
            events = [e async for e in runtime.stream("run")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(children) == 1 and children[0].process.returncode == -sent
            assert not runtime._process_manager._sessions
            with sqlite3.connect(database) as db:
                (encoded,) = db.execute(
                    "SELECT result_json FROM tool_executions WHERE tool_name=?",
                    ("write_stdin" if poll else "exec_command",),
                ).fetchone()
            result = json.loads(encoded)
            assert result["code_mode_output"]["value"]["exit_code"] == expected
            assert f"Process exited with code {expected}" in result["content"]
            assert f"Process exited with code {expected}" in result["display_content"]
            assert not result["is_error"] and not result.get("dispatch_error", False)
        finally:
            await runtime.aclose()
        cold = await create(runtime.thread_id)
        try:
            events = [e async for e in cold.stream("continue")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == steps + 2 and len(children) == 1
        finally:
            await cold.aclose()

    asyncio.run(scenario())
