import json

import pytest

from corki.context.tokens import estimate_item_tokens
from corki.core.checkpoint import checkpoint_serializer
from corki.history_notes.archive import project_history
from corki.memory.transcript import render_transcript
from corki.models.hosted_items import HostedItems
from corki.protocol.hosted import decode_hosted_payload
from corki.protocol.ids import new_thread_id, new_tool_call_id, new_turn_id
from corki.protocol.items import (
    HostedToolItem,
    ToolCallItem,
    item_from_payload,
    item_to_payload,
    new_step_id,
)
from corki.protocol.tools import ToolCall


@pytest.mark.parametrize("flag", [False, True])
def test_native_call_fact_is_checkpoint_safe_and_legacy_json_is_unchanged(flag):
    item = ToolCallItem(
        ToolCall(new_tool_call_id(), "tool_search", {}),
        new_turn_id(),
        new_step_id(),
        contains_external_context=flag,
    )
    payload = item_to_payload(item)
    assert ("contains_external_context" in payload) == flag
    assert item_from_payload("tool_call", payload) == item
    codec = checkpoint_serializer()
    assert codec.loads_typed(codec.dumps_typed(item)) == item


def test_hosted_fact_roundtrips_and_reaches_bounded_history_and_extraction():
    payload = {
        "type": "function_call_output",
        "namespace": "docs",
        "name": "notify",
        "output": "external UNIQUE_EVIDENCE",
        "call_id": None,
    }
    item = HostedToolItem(json.dumps(payload), new_turn_id(), new_step_id())
    assert item_from_payload("hosted_tool", item_to_payload(item)) == item
    codec = checkpoint_serializer()
    assert codec.loads_typed(codec.dumps_typed(item)) == item
    assert 12 < estimate_item_tokens(item) < 10_000
    assert "UNIQUE_EVIDENCE" in render_transcript((item,), redact=lambda value: value)
    row = list(project_history((item,), new_thread_id()))[0]
    assert row["role"] == "tool" and row["tool_name"] == "notify"
    assert "UNIQUE_EVIDENCE" in row["text"]


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "function_call_output", "call_id": "local", "output": "not a notification"},
        {"type": "function_call_output", "output": 42},
        {"type": "tool_search_output", "tools": []},
        {"type": "tool_search_call", "execution": "server"},
        {"type": "web_search_call", "action": []},
        {"type": "web_search_call", "id": 123},
        {"type": "web_search_call", "action": {"value": float("nan")}},
        {"type": "function_call_output", "output": [{"type": "input_text", "text": 3}]},
    ],
)
def test_hosted_contract_rejects_invalid_and_oversized_records(payload):
    with pytest.raises(ValueError):
        decode_hosted_payload(json.dumps(payload, ensure_ascii=False))


def test_hosted_completion_deduplicates_and_rejects_changed_output():
    from corki.models import ModelError

    completed = HostedItems(new_turn_id(), new_step_id())
    payload = {"type": "web_search_call", "id": "web-1", "status": "completed"}
    item = completed.complete(payload, {"output_index": 0})
    assert item is not None
    assert completed.complete(dict(payload), {"output_index": 0}) is None
    assert completed.chars == len(item.payload_json)
    with pytest.raises(ModelError, match="changed"):
        completed.complete({**payload, "status": "failed"}, {"output_index": 0})
