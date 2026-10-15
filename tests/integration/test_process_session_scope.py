"""Copied or resumed history does not grant ownership of an old live terminal."""

import asyncio
import os
import re

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX shell read fixture")


@pytest.mark.parametrize("fork", [False, True])
def test_old_terminal_identity_does_not_transfer_to_new_runtime(tmp_path, fork):
    async def scenario():
        session_id = None
        requests = []

        class Model:
            def __init__(self, start=False):
                self.start, self.calls = start, 0

            async def stream(self, request):
                self.calls += 1
                requests.append(request)
                if self.calls % 2 == 0:
                    yield ModelCompleted(())
                    return
                if self.start and self.calls == 1:
                    name, args = (
                        "exec_command",
                        {
                            "cmd": "printf READY; read line; printf FINISHED",
                            "login": False,
                            "tty": True,
                            "yield_time_ms": 0,
                        },
                    )
                else:
                    name, args = (
                        "write_stdin",
                        {
                            "session_id": session_id,
                            "chars": "finish\n",
                            "yield_time_ms": 0,
                        },
                    )
                yield ModelCompleted(
                    (
                        ToolCallItem(
                            ToolCall(new_tool_call_id(), name, args),
                            request.items[-1].turn_id,
                            new_step_id(),
                        ),
                    )
                )

            async def aclose(self):
                pass

        async def create(start=False, **kwargs):
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False),
                database_path=tmp_path / "state.db",
                home_path=tmp_path,
                model=Model(start),
                **kwargs,
            )

        async def run(runtime):
            events = [e async for e in runtime.stream("continue")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            history = await runtime._repository.load_items(runtime.thread_id)
            return history, next(i for i in reversed(history) if isinstance(i, ToolResultItem))

        source = await create(start=True)
        target = None
        try:
            original, result = await run(source)
            assert not result.is_error and "READY" in result.content
            session_id = re.search(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", result.content)[
                0
            ]
            process = source._process_manager._sessions[session_id].process
            assert process.returncode is None
            if not fork:
                await source.aclose()
                assert process.returncode is not None
            target = await create(
                **{
                    "fork_from_thread_id" if fork else "thread_id": source.thread_id,
                }
            )
            assert [e async for e in target.resume_pending()] == []
            history, result = await run(target)
            assert result.is_error and "unknown or completed process session" in result.content
            assert not target._process_manager._sessions
            assert (
                sum(isinstance(i, ToolCallItem) and i.call.name == "exec_command" for i in history)
                == 1
            )
            if fork:
                assert await source._repository.load_items(source.thread_id) == original
                assert process.returncode is None
                _, result = await run(source)
                assert not result.is_error and "FINISHED" in result.content
            else:
                assert history[: len(original)] == original
            assert len(requests) == (6 if fork else 4)
        finally:
            if target is not None:
                await target.aclose()
            await source.aclose()

    asyncio.run(scenario())
