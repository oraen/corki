"""Pending native tool migration and exact arguments survive real cold recovery."""

import asyncio
import json

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize("number", [None, -(2**100), 2**512])
def test_pending_call_cold_recovery_keeps_identity_and_never_rebinds_old_name(tmp_path, number):
    async def scenario():
        requests, executions = [], []
        legacy = number is None
        name = "memory_read" if legacy else "probe"
        arguments = {"path": "a.md"} if legacy else {"nested": [{"value": number}]}
        raw = json.dumps(arguments, separators=(",", ":"))
        call = ToolCall(new_tool_call_id(), name, arguments, raw_arguments=raw)

        class Tool:
            spec = ToolSpec(name, "pending fixture", {"type": "object"})

            async def execute(self, received, context):
                executions.append(received)
                assert received == call
                if not legacy:
                    assert type(received.arguments["nested"][0]["value"]) is int
                return ToolResult(received.id, received.name, "exact argument accepted")

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    result = next(
                        item for item in reversed(request.items) if isinstance(item, ToolResultItem)
                    )
                    assert result.call_id == call.id and result.tool_name == name
                    assert result.is_error is legacy
                    if legacy:
                        assert (
                            "unknown" in result.content.lower()
                            or "unavailable" in result.content.lower()
                        )
                    else:
                        assert result.content == "exact argument accepted"
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        class Sink:
            async def emit(self, event):
                pass

        async def create(thread=None):
            registry = ToolRegistry()
            if thread is None or not legacy:
                registry.register(Tool())
            settings = CorkiSettings(
                tmp_path,
                skills_enabled=False,
                memories_enabled=thread is not None and legacy,
                memories_generate=False,
                memories_background_enabled=False,
                memories_dedicated_tools=True,
            )
            return await LangGraphRuntime.acreate(
                settings=settings,
                model=Model(),
                registry=registry,
                database_path=tmp_path / "sessions.db",
                memory_root=tmp_path / "memories",
                thread_id=thread,
            ), settings

        runtime, settings = await create()
        try:
            await runtime._ensure_ready()
            thread, turn = runtime.thread_id, new_turn_id()
            user = UserMessageItem("inspect pending tool", turn)
            await runtime._repository.save_turn(
                TurnRecord(turn, thread, TurnStatus.RUNNING, user.content)
            )
            config = runtime._graph_config(turn)
            await runtime._compiled.ainvoke(
                _initial_state(thread, turn, settings, user),
                config=config,
                context=GraphRunContext(events=Sink()),
                interrupt_before=["execute_tools"],
            )
            checkpoint = await runtime._compiled.aget_state(config)
            assert checkpoint.next == ("execute_tools",)
            assert not executions and len(requests) == 1
            before = await runtime._repository.load_items(thread)
            assert next(i for i in before if isinstance(i, ToolCallItem)).call == call
            await runtime.aclose()
            runtime, _ = await create(thread)
            events = [event async for event in runtime.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert executions == ([] if legacy else [call])
            assert len(requests) == 2
            after = await runtime._repository.load_items(thread)
            assert after[: len(before)] == before
            assert sum(isinstance(i, ToolCallItem) for i in after) == 1
            assert sum(isinstance(i, ToolResultItem) for i in after) == 1
            assert [event async for event in runtime.resume_pending()] == []
            assert executions == ([] if legacy else [call])
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
