"""Opaque Responses checkpoints, distinct from local plaintext summaries."""

from corki.protocol.response_items import response_item_payload
from corki.protocol.wire_json import loads_wire, materialize


def compaction_payload(value: str) -> dict:
    """Validate durable opaque output without interpreting encrypted content."""
    payload = response_item_payload(materialize(loads_wire(value)))
    if payload["type"] != "compaction":
        raise ValueError("invalid remote compaction payload")
    return payload
