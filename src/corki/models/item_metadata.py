"""Responses projection of durable identities; no request-time clock or history writes."""

from corki.protocol.item_metadata import item_metadata_payload
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    HostedToolItem,
    ReasoningItem,
    RemoteHistoryItem,
    ToolCallItem,
    UserMessageItem,
)
from corki.protocol.wire_json import WireObject, object_pairs
from corki.protocol.wire_numbers import dumps_wire

_PREFIXES = {
    "message": "msg",
    "reasoning": "rs",
    "function_call": "fc",
    "function_call_output": "fco",
}


def ordinary_response_item(item):
    """Ignore excluded root extensions without changing nested business content."""
    if not isinstance(item, dict):
        return item
    return WireObject(
        (key, value)
        for key, value in object_pairs(item)
        if key not in {"internal_chat_message_metadata_passthrough", "encrypted_function_args"}
    )


def capture_response_identity(item):
    """New live records retain ordinary item identity, not legacy private provenance."""
    identity = item.get("id")
    if identity is None:
        return None
    if not isinstance(identity, str):
        raise ValueError("response item id must be a string")
    return dumps_wire({"id": identity}, sort_keys=True)


def annotate_response_input(item, payload):
    if isinstance(item, (HostedToolItem, RemoteHistoryItem)) or (
        isinstance(item, CompactionItem) and item.remote_payload_json is not None
    ):
        return payload
    value = dict(payload)
    kind = value.get("type", "message")
    if isinstance(item, (AssistantMessageItem, ReasoningItem, ToolCallItem)):
        identity = item_metadata_payload(item.response_item_metadata_json).get("id")
        if identity is not None:
            value["id"] = identity
    if not value.get("id"):
        source_id = (
            item.retained_from_id or item.id if isinstance(item, UserMessageItem) else item.id
        )
        value["id"] = f"{_PREFIXES[kind]}_{source_id}"
    return value
