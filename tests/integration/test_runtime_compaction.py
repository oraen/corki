"""Automatic local compaction inside the real tool loop, not a detached manager demo."""

import asyncio

from corki.config import CorkiSettings
from corki.context import estimate_request_tokens
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import ContextCompacted, TurnCompleted
from corki.protocol.ids import ToolCallId
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools import ToolRegistry


def test_runtime_compacts_large_observation_then_continues_with_current_input(tmp_path):
    class LargeTool:
        spec = ToolSpec("large", "Read a large observation", {"type": "object"})

        async def execute(self, call, context):
            return ToolResult(call.id, call.name, "IMPORTANT FACT\n" + "x" * 16000)

    class Model:
        def __init__(self):
            self.requests = []

        async def stream(self, request):
            self.requests.append(request)
            turn, step = request.items[-1].turn_id, new_step_id()
            if (
                isinstance(request.items[-1], UserMessageItem)
                and "checkpoint compaction" in request.items[-1].content
            ):
                assert request.instructions == self.requests[0].instructions
                assert request.tools == ()
                assert any(
                    isinstance(item, ToolResultItem) and "IMPORTANT FACT" in item.content
                    for item in request.items
                )
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            "The large observation established IMPORTANT FACT.", turn, step
                        ),
                    )
                )
            elif len(self.requests) == 1:
                yield ModelCompleted(
                    (ToolCallItem(ToolCall(ToolCallId("large-result"), "large", {}), turn, step),)
                )
            else:
                assert any(
                    isinstance(item, CompactionItem) and "IMPORTANT FACT" in item.summary
                    for item in request.items
                )
                assert any(
                    isinstance(item, UserMessageItem) and item.content == "CURRENT REQUEST VERBATIM"
                    for item in request.items
                )
                yield ModelCompleted(
                    (AssistantMessageItem("completed after compaction", turn, step),)
                )

        async def aclose(self):
            pass

    async def scenario():
        model, registry = Model(), ToolRegistry()
        registry.register(LargeTool())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                context_window_tokens=8000,
                auto_compact_tokens=3000,
            ),
            database_path=tmp_path / "compaction.db",
            registry=registry,
            model=model,
        )
        try:
            events = [event async for event in runtime.stream("CURRENT REQUEST VERBATIM")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert events[-1].final_answer == "completed after compaction"
            assert sum(isinstance(event, ContextCompacted) for event in events) == 1
            assert len(model.requests) == 3
            assert all(
                estimate_request_tokens(request.instructions, request.items, request.tools) < 7600
                for request in model.requests
            )
            history = await runtime._repository.load_items(runtime.thread_id)
            assert sum(isinstance(item, ToolCallItem) for item in history) == 1
            assert sum(isinstance(item, ToolResultItem) for item in history) == 1
            assert any(
                isinstance(item, ToolResultItem) and len(item.content) > 16000 for item in history
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
