"""Typed Responses SSE envelope decoding, distinct from Chat Completions."""

from contextlib import suppress

from corki.models.base import ModelError
from corki.models.item_metadata import ordinary_response_item
from corki.protocol.wire_json import (
    WireObject,
    check_fields,
    is_i64,
    loads_wire,
    materialize,
    object_pairs,
    validate_buffered_value,
)

_STRINGS = ("item_id", "call_id", "delta", "text")
_INDICES = ("summary_index", "content_index")
_VALUES = ("headers", "metadata", "response", "item", "safety_buffering")


def decode_response_event(data: str) -> dict:
    try:
        source = loads_wire(data)
        check_fields(source, ("type", *_STRINGS, *_INDICES, *_VALUES))
        kind = materialize(source.get("type"))
        if not isinstance(kind, str):
            raise ValueError("Responses event requires type string")
        result = {"type": kind}
        for field in (*_STRINGS, *_INDICES, *_VALUES):
            if field not in source:
                continue
            raw = source[field]
            if field == "item":
                raw = ordinary_response_item(raw)
            elif field == "response" and isinstance(raw, dict):
                raw = WireObject(
                    (
                        key,
                        [ordinary_response_item(item) for item in value]
                        if key == "output" and isinstance(value, list)
                        else value,
                    )
                    for key, value in object_pairs(raw)
                    if key != "usage_metadata"
                )
                usage = raw.get("usage")
                if isinstance(usage, dict):
                    raw = WireObject(
                        (
                            key,
                            WireObject(
                                (name, count)
                                for name, count in object_pairs(usage)
                                if name != "codex_rollout_budget_units"
                            )
                            if key == "usage"
                            else value,
                        )
                        for key, value in object_pairs(raw)
                    )
            value = materialize(raw, depth=2, preserve_pairs=False)
            if value is not None:
                if field in _STRINGS and not isinstance(value, str):
                    raise ValueError("invalid Responses string field")
                if field in _INDICES and not is_i64(source[field]):
                    raise ValueError("invalid Responses index field")
            result[field] = value
        if kind in ("response.output_item.added", "response.output_item.done"):
            validate_buffered_value(result.get("item"))
        # Compatibility providers can use index-only item identities. Codex's
        # native envelope ignores this extension; it is not a typed duplicate field.
        if "output_index" in source:
            with suppress(ValueError):
                result["output_index"] = materialize(source["output_index"], preserve_pairs=False)
        return result
    except (ValueError, RecursionError) as error:
        raise ModelError("invalid Responses streaming JSON") from error
