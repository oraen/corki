"""Automatic local compaction inside the real tool loop, not a detached manager demo."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.context import estimate_request_tokens
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted
from corki.protocol.events import ContextCompacted, TurnCompleted
from corki.protocol.ids import ToolCallId, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    "boundary", ["normal", "before-commit", "inside-transaction", "after-commit"]
)
def test_runtime_compacts_large_observation_then_continues_with_current_input(tmp_path, boundary):
    effects = []

    class LargeTool:
        spec = ToolSpec("large", "Read a large observation", {"type": "object"})

        async def execute(self, call, context):
            effects.append("once")
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
            if boundary == "normal":
                events = [event async for event in runtime.stream("CURRENT REQUEST VERBATIM")]
            else:
                await runtime._ensure_ready()
                thread, turn = runtime.thread_id, new_turn_id()
                user = UserMessageItem("CURRENT REQUEST VERBATIM", turn)
                await runtime._repository.save_turn(
                    TurnRecord(turn, thread, TurnStatus.RUNNING, user.content)
                )
                await runtime._repository.append_items(thread, (user,))
                append = runtime._repository.append_items
                install = runtime._repository._append_items_in_connection

                async def failed_append(thread_id, items):
                    if any(isinstance(item, CompactionItem) for item in items):
                        if boundary == "after-commit":
                            await append(thread_id, items)
                        raise OSError("automatic install boundary")
                    await append(thread_id, items)

                def failed_install(connection, thread_id, items):
                    if items and isinstance(items[0], CompactionItem):
                        install(connection, thread_id, items[:1])
                        raise OSError("automatic install boundary")
                    install(connection, thread_id, items)

                class Sink:
                    async def emit(self, event):
                        pass

                if boundary == "inside-transaction":
                    runtime._repository._append_items_in_connection = failed_install
                else:
                    runtime._repository.append_items = failed_append
                with pytest.raises(OSError, match="automatic install boundary"):
                    await runtime._compiled.ainvoke(
                        _initial_state(thread, turn, runtime._settings, user),
                        context=GraphRunContext(events=Sink()),
                        config=runtime._graph_config(turn),
                        durability="sync",
                    )
                before = await runtime._repository.load_items(thread)
                assert sum(isinstance(item, CompactionItem) for item in before) == (
                    boundary == "after-commit"
                )
                settings = runtime._settings
                await runtime.aclose()
                registry = ToolRegistry()
                registry.register(LargeTool())
                runtime = await LangGraphRuntime.acreate(
                    settings=settings,
                    database_path=tmp_path / "compaction.db",
                    registry=registry,
                    model=model,
                    thread_id=thread,
                )
                events = [event async for event in runtime.resume_pending()]
                restored = await runtime._repository.load_items(thread)
                assert tuple(restored[: len(before)]) == tuple(before)
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert events[-1].final_answer == "completed after compaction"
            assert sum(isinstance(event, ContextCompacted) for event in events) == (
                boundary != "after-commit"
            )
            assert len(model.requests) == (
                4 if boundary in {"before-commit", "inside-transaction"} else 3
            )
            assert effects == ["once"]
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
