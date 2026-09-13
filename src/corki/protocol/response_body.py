"""Typed native message bodies, independent of UI text and item provenance."""

from corki.protocol.response_items import response_item_from_value
from corki.protocol.wire_json import loads_wire, materialize
from corki.protocol.wire_numbers import dumps_wire


def _body(source, kind):
    if kind not in {"message", "reasoning"} or not isinstance(source, dict):
        raise ValueError("invalid native response body")
    fields = ("content",) if kind == "message" else ("summary", "content")
    typed = response_item_from_value(
        {
            "type": kind,
            **({"role": "assistant"} if kind == "message" else {}),
            **{key: source[key] for key in fields if key in source},
        }
    )
    return {key: typed[key] for key in fields if key in typed}


def capture_response_body(item):
    """Only known body fields survive; provider extensions do not become history."""
    return dumps_wire(_body(item, item["type"]), sort_keys=True)


def response_body_payload(value, kind):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("native response body must be a JSON string")
    return _body(materialize(loads_wire(value), preserve_pairs=False), kind)
