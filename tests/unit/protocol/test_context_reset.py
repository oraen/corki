import asyncio
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.context.history import active_history
from corki.core.checkpoint import checkpoint_serializer
from corki.protocol.ids import ThreadId, TurnId, new_tool_call_id
from corki.protocol.items import (
    CompactionItem,
    ToolResultItem,
    UserMessageItem,
    item_from_payload,
    item_kind,
    item_to_payload,
)
from corki.protocol.tools import ToolCall, ToolResult, ToolStateUpdate
from corki.storage import SQLiteSessionRepository


def test_reset_and_tool_request_roundtrip_without_changing_legacy_payloads(tmp_path):
    async def scenario():
        thread, turn = ThreadId("thread"), TurnId("turn")
        user = UserMessageItem("raw history", turn)
        marker = CompactionItem("", user.id, turn, context_reset=True)
        request = ToolResultItem(
            new_tool_call_id(),
            "new_context",
            "requested",
            turn,
            state_update=ToolStateUpdate(new_context_requested=True),
        )
        serializer = checkpoint_serializer()
        for item in (marker, request):
            assert item_from_payload(item_kind(item), item_to_payload(item)) == item
            assert serializer.loads_typed(serializer.dumps_typed(item)) == item
        assert "context_reset" not in item_to_payload(replace(marker, context_reset=False))
        assert (
            "new_context_requested"
            not in item_to_payload(replace(request, state_update=ToolStateUpdate()))["state_update"]
        )
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        await repository.create_thread(thread, tmp_path)
        await repository.append_items(thread, (user, marker))
        assert active_history(await repository.load_items(thread)) == ()
        assert len(await repository.load_items(thread)) == 2
        assert [message.content for message in await repository.load_messages(thread)] == [
            "raw history"
        ]
        call = ToolCall(request.call_id, "new_context", {})
        assert await repository.claim_tool_call(thread, turn, call) is None
        result = ToolResult(call.id, call.name, "requested", state_update=request.state_update)
        await repository.complete_tool_call(thread, turn, result)
        reopened = SQLiteSessionRepository(tmp_path / "sessions.db")
        assert await reopened.claim_tool_call(thread, turn, call) == result

    asyncio.run(scenario())


@pytest.mark.parametrize("enabled", [False, True])
def test_explicit_feature_boolean_configuration(tmp_path, enabled):
    config = tmp_path / "config.toml"
    config.write_text(f"[features]\ntoken_budget = {str(enabled).lower()}\n")
    assert CorkiSettings.for_directory(tmp_path, config_file=config).token_budget_enabled is enabled


@pytest.mark.parametrize("invalid", [None, 1, [], {}, "true"])
def test_custom_or_invalid_token_budget_is_not_silently_enabled(tmp_path, invalid):
    with pytest.raises(ValueError, match="features.token_budget"):
        CorkiSettings(working_directory=tmp_path, token_budget_enabled=invalid)
