"""Compaction replacement spans retain their meaning across display read batches."""

from dataclasses import replace

import pytest

from corki.protocol.ids import new_tool_call_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall


def test_display_projection_is_independent_of_batch_boundaries():
    from corki.cli.history_projection import HistoryProjection

    first, second = new_turn_id(), new_turn_id()
    user = UserMessageItem("original", first)
    call = ToolCallItem(ToolCall(new_tool_call_id(), "lookup", {}), first, new_step_id())
    result = ToolResultItem(call.call.id, call.call.name, "original result", first)
    answer = AssistantMessageItem("original answer", first, new_step_id())
    retained = replace(user, retained_from_id=user.id)
    items = (
        user,
        call,
        result,
        answer,
        CompactionItem("summary one", None, second, replacement_item_count=3),
        retained,
        call,
        result,
        CompactionItem("summary two", None, second, replacement_item_count=1),
        answer,
        retained,
        UserMessageItem("new input", second),
    )
    expected = (*items[:4], items[-1])
    for first_cut in range(len(items) + 1):
        for second_cut in range(first_cut, len(items) + 1):
            projection = HistoryProjection()
            actual = (
                *projection.feed(items[:first_cut]),
                *projection.feed(items[first_cut:second_cut]),
                *projection.feed(items[second_cut:]),
            )
            assert actual == expected
            assert projection.remaining_replacements == 0


def test_projection_can_resume_inside_a_replacement_span():
    from corki.cli.history_projection import HistoryProjection

    turn = new_turn_id()
    projection = HistoryProjection()
    assert projection.feed((CompactionItem("summary", None, turn, replacement_item_count=2),)) == ()
    resumed = HistoryProjection(projection.remaining_replacements)
    copied = AssistantMessageItem("retained answer", turn, new_step_id())
    fresh = UserMessageItem("fresh", turn)
    assert resumed.feed((copied,)) == ()
    assert resumed.feed((copied, fresh)) == (fresh,)


@pytest.mark.parametrize("remaining", [-1, True, 1.5, "2"])
def test_projection_rejects_invalid_boundary_state(remaining):
    from corki.cli.history_projection import HistoryProjection

    with pytest.raises(ValueError):
        HistoryProjection(remaining)
