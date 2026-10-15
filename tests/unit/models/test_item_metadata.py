"""Stable legacy identities, typed provenance and metadata budget boundaries."""

import asyncio
import base64
import json
from dataclasses import replace

import pytest

from corki.context.tokens import estimate_item_tokens
from corki.core.checkpoint import checkpoint_serializer
from corki.models.request_metadata import filter_request_metadata
from corki.models.responses import _to_response_input
from corki.protocol.item_metadata import capture_item_metadata
from corki.protocol.items import (
    AssistantMessageItem,
    ReasoningItem,
    ToolCallItem,
    item_from_payload,
    item_kind,
    item_to_payload,
)
from corki.protocol.tools import ToolCall
from corki.storage import SQLiteSessionRepository

META = "internal_chat_message_metadata_passthrough"

# MsgPack emitted by the immutable batch138 wheel, before the new field existed.
# This verifies genuinely old bytes, not just a roundtrip through the new writer.
OLD_CHECKPOINTS = {
    "assistant": """
x5kCk7Rjb3JraS5wcm90b2NvbC5pdGVtc7RBc3Npc3RhbnRNZXNzYWdlSXRlbYenY29udGVudKR0ZXh0
p3R1cm5faWSkdHVybqdzdGVwX2lkpHN0ZXCiaWSlbG9jYWyqY3JlYXRlZF9hdLkyMDI2LTA5LTA4VDAw
OjAwOjAwKzAwOjAwr21lbW9yeV9jaXRhdGlvbsClcGhhc2XA
""",
    "reasoning": """
x70Ck7Rjb3JraS5wcm90b2NvbC5pdGVtc61SZWFzb25pbmdJdGVtiadjb250ZW50pHRleHSndHVybl9pZKR0
dXJup3N0ZXBfaWSkc3RlcKJpZKVsb2NhbKdzdW1tYXJ5wK1wcm92aWRlcl9uYW1lwLBwcm92aWRlcl9pdGVt
X2lkwLFlbmNyeXB0ZWRfY29udGVudKZvcGFxdWWqY3JlYXRlZF9hdLkyMDI2LTA5LTA4VDAwOjAwOjAwKzAwOjAw
""",
    "call": """
yAEgApO0Y29ya2kucHJvdG9jb2wuaXRlbXOsVG9vbENhbGxJdGVthqRjYWxsx5ECk7Rjb3JraS5wcm90b2Nv
bC50b29sc6hUb29sQ2FsbIaiaWSkY2FsbKRuYW1lpXByb2JlqWFyZ3VtZW50c4CtcmF3X2FyZ3VtZW50c6Cr
cGFyc2VfZXJyb3LAqmlucHV0X2tpbmTHKQCTtGNvcmtpLnByb3RvY29sLnRvb2xzrVRvb2xJbnB1dEtpbmSk
anNvbqd0dXJuX2lkpHR1cm6nc3RlcF9pZKRzdGVwomlkpWxvY2FsqmNyZWF0ZWRfYXS5MjAyNi0wOS0wOFQw
MDowMDowMCswMDowMLljb250YWluc19leHRlcm5hbF9jb250ZXh0wg==
""",
}


def canonical(kind, **fields):
    if kind == "assistant":
        return AssistantMessageItem("text", "turn", "step", **fields)
    if kind == "reasoning":
        return ReasoningItem("text", "turn", "step", encrypted_content="opaque", **fields)
    return ToolCallItem(ToolCall("call", "probe", {}), "turn", "step", **fields)


@pytest.mark.parametrize("boundary", ["added", "done", "completed"])
@pytest.mark.parametrize("private", ['{"turn_id":[]}', '"\\ud800"', '{"value":1e1000000}'])
def test_live_private_fields_are_ignored_before_value_decoding(boundary, private):
    from corki.models.response_wire import decode_response_event

    item = {
        "type": "function_call",
        "id": "fc_one",
        "call_id": "one",
        "name": "probe",
        "arguments": json.dumps({META: "business", "encrypted_function_args": "business"}),
    }
    raw = json.dumps(item)[:-1] + f',"{META}":{private},"encrypted_function_args":false}}'
    data = (
        '{"type":"response.completed","response":{"id":"r","output":[' + raw + "]}}"
        if boundary == "completed"
        else '{"type":"response.output_item.' + boundary + '","item":' + raw + "}"
    )
    event = decode_response_event(data)
    assert (event["response"]["output"][0] if boundary == "completed" else event["item"]) == item


def test_grouped_context_serialization_preserves_order_without_private_fields():
    from corki.models.responses import _to_response_message
    from corki.protocol.items import ContextItem, ContextRole

    items = tuple(
        ContextItem(key, ContextRole.DEVELOPER, key, "turn", content_kind="private.kind")
        for key in ("first", "second")
    )
    assert _to_response_message(items) == {
        "role": "developer",
        "id": f"msg_{items[0].id}",
        "content": [{"type": "input_text", "text": key} for key in ("first", "second")],
    }


@pytest.mark.parametrize("kind", ["assistant", "reasoning", "call"])
@pytest.mark.parametrize("identity", [None, "", "legacy", "_bad", "bad_", "any_good", "x__"])
def test_server_ids_do_not_replace_storage_or_execution_identity(kind, identity):
    saved = capture_item_metadata(
        {
            "type": "function_call",
            "id": identity,
            META: {"turn_id": "", "create_time": 0},
            "encrypted_function_args": ["do-not-send"],
        }
    )
    item = canonical(kind, id="local", response_item_metadata_json=saved)
    payload = _to_response_input(item)
    assert META not in payload
    assert "encrypted_function_args" not in payload
    expected = {"assistant": "msg_local", "reasoning": "rs_local", "call": "fc_local"}[kind]
    wire = filter_request_metadata([payload], supported=True, content_item_kinds=True)[0]
    if identity:
        assert wire.get("id") == (identity if identity in {"any_good", "x__"} else None)
    else:
        assert wire["id"] == expected
    assert META not in wire
    assert item.id == "local" and item.response_item_metadata_json == saved
    if kind == "call":
        assert wire["call_id"] == "call"


@pytest.mark.parametrize("kind", ["assistant", "reasoning", "call"])
def test_old_payload_and_checkpoint_remain_idempotent_without_new_field(tmp_path, kind):
    async def scenario():
        item = canonical(kind, id="local", created_at="2026-09-08T00:00:00+00:00")
        old_payload = item_to_payload(item)
        assert "response_item_metadata_json" not in old_payload
        restored = item_from_payload(item_kind(item), old_payload)
        serializer = checkpoint_serializer()
        assert serializer.loads_typed(("msgpack", base64.b64decode(OLD_CHECKPOINTS[kind]))) == item
        if kind != "call":
            assert item.response_body_json is None
            assert "response_body_json" not in old_payload
        assert serializer.loads_typed(serializer.dumps_typed(restored)) == item
        repository = SQLiteSessionRepository(tmp_path / "s.db")
        try:
            await repository.create_thread("thread", tmp_path)
            await repository.append_items("thread", (restored,))
            await repository.append_items("thread", (replace(restored, created_at="later"),))
            loaded = await repository.load_items("thread")
            assert loaded == (item,)
            wire = _to_response_input(loaded[0])
            assert META not in wire
        finally:
            await repository.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["assistant", "call"])
def test_archive_only_metadata_has_no_wire_budget(kind):
    item = canonical(kind)
    value = capture_item_metadata({META: {"turn_id": "x" * 4000}})
    large = replace(item, response_item_metadata_json=value)
    assert estimate_item_tokens(large) == estimate_item_tokens(item)


@pytest.mark.parametrize("kind", ["assistant", "reasoning", "call"])
@pytest.mark.parametrize(
    "value", [[], "[]", '{"id":3}', '{"internal_chat_message_metadata_passthrough":{"turn_id":3}}']
)
def test_invalid_persisted_metadata_is_rejected_at_canonical_boundary(kind, value):
    with pytest.raises(ValueError):
        canonical(kind, response_item_metadata_json=value)
