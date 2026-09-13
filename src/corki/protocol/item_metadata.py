"""Immutable server item provenance, separate from host execution identity."""

from corki.protocol.response_items import METADATA, response_metadata_payload
from corki.protocol.wire_json import loads_wire, materialize
from corki.protocol.wire_numbers import dumps_wire


def capture_item_metadata(item: dict) -> str | None:
    """Keep typed replay fields, never server-supplied host execution records."""
    result = {}
    if item.get("id") is not None:
        if not isinstance(item["id"], str):
            raise ValueError("response item id must be a string")
        result["id"] = item["id"]
    if item.get(METADATA) is not None:
        result[METADATA] = response_metadata_payload(item[METADATA])
    if item.get("type") == "function_call" and item.get("encrypted_function_args") is not None:
        value = item["encrypted_function_args"]
        if not isinstance(value, list) or any(not isinstance(part, str) for part in value):
            raise ValueError("encrypted function args must be strings")
        result["encrypted_function_args"] = value
    return dumps_wire(result, sort_keys=True) if result else None


def item_metadata_payload(value: str | None) -> dict:
    if value is None:
        return {}
    if not isinstance(value, str):
        raise ValueError("response item metadata must be a JSON string")
    payload = materialize(loads_wire(value), preserve_pairs=False)
    if not isinstance(payload, dict):
        raise ValueError("response item metadata must be an object")
    # The envelope is already an internal projection, not an external item variant.
    normalized = capture_item_metadata({**payload, "type": "function_call"})
    return materialize(loads_wire(normalized), preserve_pairs=False) if normalized else {}
