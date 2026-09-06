from corki.protocol.ids import new_tool_call_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ContextItem,
    ContextRole,
    ReasoningItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    item_from_payload,
    item_kind,
    item_to_payload,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolStateUpdate


def test_every_conversation_item_round_trips_through_json_payload() -> None:
    turn_id = new_turn_id()
    step_id = new_step_id()
    call_id = new_tool_call_id()
    call = ToolCall(call_id, "exec_command", {"cmd": "pwd"}, '{"cmd":"pwd"}')
    user = UserMessageItem("hello", turn_id)
    items = (
        user,
        AssistantMessageItem("answer", turn_id, step_id),
        ReasoningItem("reasoning", turn_id, step_id, summary="summary"),
        ToolCallItem(call, turn_id, step_id),
        ToolResultItem(
            call_id,
            "exec_command",
            "done",
            turn_id,
            state_update=ToolStateUpdate(plan=({"step": "verify", "status": "in_progress"},)),
        ),
        ContextItem("project", ContextRole.USER, "facts", turn_id),
        CompactionItem(
            "summary",
            user.id,
            turn_id,
            replacement_item_count=2,
            summary_insert_index=1,
        ),
    )

    restored = tuple(item_from_payload(item_kind(item), item_to_payload(item)) for item in items)

    assert restored == items
