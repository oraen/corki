import asyncio
import json
import os
import re
import shlex
import sys

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX real shell/PTY fixture")


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize(
    "kind,requested,expected",
    [
        ("exec", 0, 0.25),
        ("exec", 1000000, 30),
        ("empty", 0, 5),
        ("empty", 1000000, 11),
        ("empty", None, 5),
        ("input", None, 0.25),
        ("input", 1000000, 30),
    ],
)
def test_runtime_shell_wait_contract_and_cold_no_replay(
    tmp_path, nested, kind, requested, expected
):
    async def scenario():
        requests, waits, spawns = [], [], []
        command = "exec " + shlex.join([sys.executable, "-c", "import time; time.sleep(20)"])
        tool_steps = 1 if kind == "exec" else 2

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) <= tool_steps:
                    if len(requests) == 1:
                        name = "exec_command"
                        args = {
                            "cmd": command,
                            "tty": kind == "input",
                            "login": False,
                            "yield_time_ms": requested if kind == "exec" else 0,
                        }
                    else:
                        result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                        assert not result.is_error, result.content
                        session = re.search(
                            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", result.content
                        )[0]
                        name, args = "write_stdin", {"session_id": session}
                        if kind == "input":
                            args["chars"] = "input\n"
                        if requested is not None:
                            args["yield_time_ms"] = requested
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
                    assert not result.is_error, result.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            command_yield_seconds=17,
            background_terminal_max_timeout=11000,
            tool_mode="code_mode" if nested else "direct",
        )

        def create(thread=None):
            runtime = LangGraphRuntime.create(
                settings=settings,
                database_path=tmp_path / "yield.db",
                model=Model(),
                thread_id=thread,
            )
            manager = runtime._process_manager
            start = manager._start_session
            wait = manager._wait_session

            async def counted_start(*args, **kwargs):
                spawns.append(True)
                return await start(*args, **kwargs)

            async def capture_wait(session, seconds):
                if manager._sessions.get(session.id) is not session:
                    # Let the sandbox outcome check finish before observing the
                    # model-requested wait for the published terminal.
                    return await wait(session, seconds)
                waits.append(seconds)
                await asyncio.sleep(0)

            manager._start_session = counted_start
            manager._wait_session = capture_wait
            return runtime

        runtime = create()
        try:
            async with asyncio.timeout(4):
                events = [e async for e in runtime.stream("execute")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert waits == ([expected] if kind == "exec" else [0.25, expected])
        finally:
            await runtime.aclose()
        cold = create(runtime.thread_id)
        try:
            events = [e async for e in cold.stream("continue")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(requests) == tool_steps + 2 and spawns == [True]
            assert len(waits) == tool_steps
        finally:
            await cold.aclose()

    asyncio.run(scenario())
