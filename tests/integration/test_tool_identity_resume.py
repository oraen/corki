"""The actual checkpoint recovery route must never reuse a different invocation."""

import asyncio
import json

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_thread_id, new_tool_call_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolRegistry


@pytest.mark.parametrize("completed", [False, True], ids=["unknown", "completed"])
@pytest.mark.parametrize(
    "raw,changed",
    [
        ('{"path":"one.txt"}', '{"path":"two.txt"}'),
        ('{"n":1.234567890123456781}', '{"n":1.234567890123456789}'),
        ('{"n":1e-999}', '{"n":2e-999}'),
    ],
    ids=["path", "rounded-decimal", "underflow"],
)
def test_resumed_committed_step_does_not_use_mismatched_cached_result(
    tmp_path, completed, raw, changed
):
    async def scenario():
        database = tmp_path / "sessions.db"
        repository = SQLiteSessionRepository(database)
        thread, turn, call_id = new_thread_id(), new_turn_id(), new_tool_call_id()
        await repository.create_thread(thread, tmp_path)
        await repository.save_turn(TurnRecord(turn, thread, TurnStatus.RUNNING, "write two"))
        await repository.append_items(thread, (UserMessageItem("write two", turn),))
        old = ToolCall(call_id, "write", json.loads(raw), raw_arguments=raw)
        await repository.claim_tool_call(thread, turn, old)
        if completed:
            await repository.complete_tool_call(
                thread, turn, ToolResult(call_id, "write", "wrote one.txt")
            )
        current = ToolCall(call_id, "write", json.loads(changed), raw_arguments=changed)
        if "n" in old.arguments:
            assert old.arguments == current.arguments
        await repository.commit_model_step(
            thread, turn, 0, ModelCompleted((ToolCallItem(current, turn, new_step_id()),))
        )
        repository = SQLiteSessionRepository(database)

        class Tool:
            spec = ToolSpec("write", "write fixture", {"type": "object"})
            calls = 0

            async def execute(self, call, context):
                self.calls += 1
                raise AssertionError("ambiguous side effect must not execute")

        class Model:
            calls = 0

            async def stream(self, request):
                self.calls += 1
                result = next(
                    item for item in reversed(request.items) if isinstance(item, ToolResultItem)
                )
                assert result.is_error and "collision" in result.content
                assert "wrote one.txt" not in result.content
                yield ModelCompleted(
                    (AssistantMessageItem("cannot confirm write", turn, new_step_id()),)
                )

            async def aclose(self):
                pass

        registry, tool, model = ToolRegistry(), Tool(), Model()
        registry.register(tool)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=database,
            repository=repository,
            thread_id=thread,
            registry=registry,
            model=model,
        )
        try:
            events = [event async for event in runtime.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert tool.calls == 0 and model.calls == 1
            old_result = await repository.claim_tool_call(thread, turn, old)
            if completed:
                assert old_result.content == "wrote one.txt" and not old_result.is_error
            else:
                assert old_result.is_error and "unknown" in old_result.content
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
