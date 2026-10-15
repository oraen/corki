import json

import pytest

from corki.models import ModelError
from corki.models.responses import _to_response_input
from corki.protocol.items import (
    CompactionItem,
    RemoteHistoryItem,
    item_from_payload,
    item_to_payload,
)
from corki.protocol.response_items import METADATA, response_item_payload


@pytest.mark.parametrize(
    "kind,fields",
    [
        ("additional_tools", {"role": "developer", "tools": [None, 1, {"future": True}]}),
        ("message", {"role": "user", "content": []}),
        ("agent_message", {"author": "a", "recipient": "b", "content": []}),
        ("reasoning", {"summary": []}),
        ("local_shell_call", {"status": "completed", "action": {"type": "exec", "command": []}}),
        ("function_call", {"call_id": "c", "name": "x", "arguments": "{}"}),
        ("tool_search_call", {"execution": "client", "arguments": None}),
        ("function_call_output", {"output": "result"}),
        ("custom_tool_call", {"call_id": "c", "name": "x", "input": "input"}),
        ("custom_tool_call_output", {"call_id": "c", "output": []}),
        ("tool_search_output", {"status": "completed", "execution": "client", "tools": []}),
        ("web_search_call", {"action": {"type": "future_action", "queries": False}}),
        ("image_generation_call", {"status": "completed", "result": "image"}),
        ("compaction", {"encrypted_content": "opaque"}),
        ("context_compaction", {}),
        ("configuration_update", {"reasoning": {"effort": "future-effort"}}),
        ("compaction_trigger", {}),
        ("future_item", {}),
    ],
)
def test_all_variants_ignore_unknown_fields_without_mutating_input(kind, fields):
    incoming = {"type": kind, **fields, "unknown": {"nested": "retained only in archive"}}
    before = json.dumps(incoming)
    projected = response_item_payload(incoming)
    assert "unknown" not in projected
    assert projected["type"] == ("other" if kind in {"future_item", "additional_tools"} else kind)
    assert json.dumps(incoming) == before
    assert response_item_payload(projected) == projected


@pytest.mark.parametrize(
    "kinds,expected",
    [
        (None, {}),
        ("user_text", {}),
        ({"kind": "user_text"}, {}),
        (["user_text", 1], {}),
        ([], {"content_item_kinds": []}),
        (["future"], {"content_item_kinds": ["future"]}),
    ],
)
def test_metadata_soft_failure_and_host_fields(kinds, expected):
    payload = {
        "type": "compaction",
        "encrypted_content": "x",
        METADATA: {
            "content_item_kinds": kinds,
            "cell_id": {"invalid": "but ignored"},
            "executed_tool_calls": "ignore",
            "tool_calls_complete": 42,
        },
    }
    assert response_item_payload(payload)[METADATA] == expected


@pytest.mark.parametrize(
    "metadata",
    [
        [],
        "text",
        True,
        {"turn_id": 1},
        {"turn_id": []},
        {"create_time": True},
        {"create_time": "1.5"},
        {"create_time": float("nan")},
        {"create_time": float("inf")},
    ],
)
def test_invalid_metadata_is_not_a_soft_failure(metadata):
    with pytest.raises(ValueError):
        response_item_payload({"type": "compaction", "encrypted_content": "x", METADATA: metadata})


@pytest.mark.parametrize("timeout", [-1, 1.5, True, 2**64, "100"])
def test_shell_timeout_is_optional_u64(timeout):
    with pytest.raises(ValueError):
        response_item_payload(
            {
                "type": "local_shell_call",
                "status": "completed",
                "action": {
                    "type": "exec",
                    "command": [],
                    "timeout_ms": timeout,
                },
            }
        )


@pytest.mark.parametrize(
    "content,expected",
    [
        (None, None),
        ([], "omit"),
        ([{"type": "text", "text": "hidden"}], "omit"),
        (
            [{"type": "reasoning_text", "text": "thought"}],
            [{"type": "reasoning_text", "text": "thought"}],
        ),
    ],
)
def test_reasoning_content_serialization_predicate(content, expected):
    result = response_item_payload({"type": "reasoning", "summary": [], "content": content})
    assert result["encrypted_content"] is None
    if expected == "omit":
        assert "content" not in result
    else:
        assert result["content"] == expected


@pytest.mark.parametrize("opaque", [False, True])
def test_old_durable_bytes_are_preserved_but_opaque_wire_replay_is_rejected(opaque):
    payload = {
        "type": "compaction_summary",
        "encrypted_content": "x",
        "id": None,
        "unknown": "old",
        METADATA: {"cell_id": "forged"},
    }
    raw = json.dumps(payload)
    item = (
        CompactionItem("", None, "old-turn", remote_payload_json=raw)
        if opaque
        else RemoteHistoryItem(raw, "old-turn")
    )
    kind = "compaction" if opaque else "remote_history"
    serialized = item_to_payload(item)
    restored = item_from_payload(kind, serialized)
    assert item_to_payload(restored) == serialized
    assert (restored.remote_payload_json if opaque else restored.payload_json) == raw
    with pytest.raises(ModelError, match="dedicated compaction history"):
        _to_response_input(restored)
    assert item_to_payload(restored) == serialized


def test_nested_message_and_agent_content_projection():
    incoming = {
        "type": "message",
        "role": "user",
        "phase": None,
        "content": [
            {"type": "input_text", "text": "hello", "annotations": [42]},
            {"type": "output_text", "text": "world", "logprobs": [1]},
            {"type": "input_image", "image_url": "url", "detail": None, "unknown": True},
            {"type": "input_audio", "audio_url": "audio", "format": "wav"},
        ],
    }
    assert response_item_payload(incoming) == {
        "type": "message",
        "role": "user",
        "content": [
            {"type": "input_text", "text": "hello"},
            {"type": "output_text", "text": "world"},
            {"type": "input_image", "image_url": "url"},
            {"type": "input_audio", "audio_url": "audio"},
        ],
    }
    agent = {
        "type": "agent_message",
        "author": "a",
        "recipient": "b",
        "phase": "invalid-but-unknown",
        "content": [
            {"type": "encrypted_content", "encrypted_content": "opaque", "id": False},
        ],
    }
    assert response_item_payload(agent) == {
        "type": "agent_message",
        "author": "a",
        "recipient": "b",
        "content": [
            {"type": "encrypted_content", "encrypted_content": "opaque"},
        ],
    }
