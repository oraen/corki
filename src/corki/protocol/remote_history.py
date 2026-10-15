"""Validated provider-normalized history, not new user input or executable output."""

from corki.protocol.response_items import response_item_payload
from corki.protocol.wire_json import loads_wire, materialize


def remote_history_payload(value: str) -> dict:
    payload = response_item_payload(materialize(loads_wire(value)))
    kind = payload["type"]
    if kind == "message":
        if payload["role"] not in ("user", "assistant"):
            raise ValueError("invalid remote history message role")
    elif kind not in ("compaction", "context_compaction", "agent_message"):
        raise ValueError("invalid remote history item type")
    return payload
