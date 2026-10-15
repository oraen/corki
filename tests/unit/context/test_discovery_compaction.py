import asyncio
import json
from pathlib import Path

from corki.context import ContextSnapshot, ContextWindowManager, active_history
from corki.context.tokens import estimate_request_tokens
from corki.models import ModelCompleted
from corki.protocol.ids import ToolCallId, new_thread_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolExposure, ToolSpec
from corki.storage import SQLiteSessionRepository
from corki.tools.discovery import build_tool_plan


def test_compaction_releases_loaded_schemas_and_rebudgets_request(tmp_path: Path):
    class SummaryModel:
        def __init__(self):
            self.requests = []

        async def stream(self, request):
            self.requests.append(request)
            yield ModelCompleted(
                (AssistantMessageItem("summary", request.items[-1].turn_id, new_step_id()),)
            )

    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "compact.db")
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        deferred = ToolSpec("big_tool", "documentation " * 400, {}, exposure=ToolExposure.DEFERRED)
        search = ToolSpec("tool_search", "discover tools", {})
        specs = (search, deferred)
        call = ToolCall(ToolCallId("search"), "tool_search", {"query": "documentation"})
        original = (
            UserMessageItem("old request", turn),
            ToolCallItem(call, turn, new_step_id()),
            ToolResultItem(
                call.id,
                call.name,
                json.dumps(deferred.as_chat_completion_tool()),
                turn,
                discovered_tools=(deferred,),
            ),
        )
        await repository.append_items(thread, original)
        model = SummaryModel()
        manager = ContextWindowManager(
            repository=repository,
            model=model,
            model_name="test",
            context_window_tokens=2_000,
            auto_compact_tokens=1_000,
        )
        new_turn = new_turn_id()
        pending = UserMessageItem(
            "CURRENT INPUT MUST REMAIN VERBATIM " + "constraint " * 250, new_turn
        )

        def tools(items):
            return build_tool_plan(specs, items, "compatible").advertised

        assert deferred.name in {spec.name for spec in tools(original)}
        prepared = await manager.prepare(
            thread_id=thread,
            turn_id=new_turn,
            snapshot=ContextSnapshot("base", (), tmp_path),
            tools=tools(original),
            pending_items=(pending,),
            tool_resolver=tools,
        )
        assert prepared.compacted
        assert tools(prepared.items) == (search,)
        assert prepared.estimated_tokens == estimate_request_tokens(
            "base", prepared.items, (search,)
        )
        assert prepared.estimated_tokens < 2_000
        assert any(
            isinstance(item, UserMessageItem) and item.content == pending.content
            for item in prepared.items
        )
        assert all(pending not in request.items for request in model.requests)
        stored = await repository.load_items(thread)
        assert stored[:3] == original
        assert active_history(stored) == prepared.items
        assert tools(active_history(stored)) == (search,)
        await repository.close()

    asyncio.run(scenario())
