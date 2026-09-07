import json

import pytest

from corki.history_notes.archive import history_action
from corki.protocol.ids import ThreadId, TurnId, new_tool_call_id
from corki.protocol.items import (
    CompactionItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ImageAttachment, TextContent, ToolCall


@pytest.mark.parametrize(
    "arguments,raw,error,kind,expected",
    [
        (None, '{"text":"界",', "invalid JSON", "json", '{"text":"界",'),
        (None, '["界"]', "tool arguments must be a JSON object", "json", '["界"]'),
        ({"n": 2}, '{ "n": 1, "n": 2 }', None, "json", '{ "n": 1, "n": 2 }'),
        (None, "print('界')\n", None, "freeform", "print('界')\n"),
        ({"text": "界"}, "", None, "json", '{"text":"界"}'),
        (None, "", "empty input", "json", ""),
    ],
)
def test_archive_preserves_original_call_input(arguments, raw, error, kind, expected):
    call = ToolCall(new_tool_call_id(), "example", arguments, raw, error, kind)
    item = ToolCallItem(call, TurnId("t"), new_step_id())
    stored = (item,)
    result = history_action(
        "read_item", {"window_id": "thread:0", "item_id": str(item.id)}, stored, "thread", 2000
    )
    assert result["text"] == expected
    assert result["call_id"] == str(call.id)
    assert result["input_kind"] == kind
    assert result.get("parse_error") == error
    found = history_action("search_contents", {"query": expected}, stored, "thread", 2000)
    assert found["items"][0]["item_id"] == str(item.id)
    assert found["items"][0]["truncated_content"] == expected


def test_windows_filters_and_tool_pair_identity_are_preserved():
    turn, thread = TurnId("t"), ThreadId("thread")
    call = ToolCall(new_tool_call_id(), "notes::read", {"path": "p"})
    user = UserMessageItem("first", turn)
    invocation = ToolCallItem(call, turn, new_step_id())
    output = ToolResultItem(call.id, call.name, "failure", turn, is_error=True)
    marker = CompactionItem("", output.id, turn, context_reset=True)
    stored = (user, invocation, output, marker, UserMessageItem("second", turn))
    windows = history_action("list_windows", {"recent_first": True}, stored, thread, 2000)
    assert [(w["window_id"], w["item_count"]) for w in windows["windows"]] == [
        ("thread:1", 1),
        ("thread:0", 3),
    ]
    result = history_action(
        "list_items", {"tool_namespace": "notes", "tool_name": "read"}, stored, thread, 2000
    )
    assert [r["call_id"] for r in result["items"]] == [str(call.id), str(call.id)]
    assert result["items"][1]["is_error"] is True
    assert (
        history_action("list_items", {"window_id": "missing"}, stored, thread, 2000)["items"] == []
    )
    assert (
        history_action("list_windows", {"agent_name": "/other"}, stored, thread, 2000)["windows"]
        == []
    )


@pytest.mark.parametrize("tool_call", [False, True])
def test_read_item_continuation_is_lossless_with_unicode_and_small_budget(tool_call):
    item = UserMessageItem('"界\\' * 300, TurnId("t"))
    original = item.content
    if tool_call:
        item = ToolCallItem(
            ToolCall(new_tool_call_id(), "example", None, original, "invalid JSON"),
            TurnId("t"),
            new_step_id(),
        )
    offset, recovered = 0, ""
    for _ in range(100):
        result = history_action(
            "read_item",
            {"window_id": "thread:0", "item_id": str(item.id), "offset_chars": offset},
            (item,),
            "thread",
            512,
        )
        assert len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()) <= 512
        recovered += result["text"]
        if result["next_offset_chars"] is None:
            break
        assert result["next_offset_chars"] > offset
        offset = result["next_offset_chars"]
    assert recovered == original


def test_archive_read_recovers_image_separately_from_text():
    item = ToolResultItem(
        new_tool_call_id(),
        "picture",
        "legacy",
        TurnId("t"),
        content_items=(
            TextContent("caption"),
            ImageAttachment("data:image/png;base64,YWJj", "original"),
        ),
    )
    result = history_action(
        "read_item", {"window_id": "thread:0", "item_id": str(item.id)}, (item,), "thread", 2000
    )
    assert result["images"] == [{"data": "YWJj", "mime_type": "image/png", "detail": "original"}]
    assert "YWJj" not in result["text"] and "caption" in result["text"]
