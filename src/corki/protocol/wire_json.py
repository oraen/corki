"""JSON syntax plus explicit typed/Value boundaries for native Responses.

Keep pairs until the caller selects typed fields; serde Value maps instead use
last-key-wins. Ignored top-level fields need not materialize numbers or strings.
"""

import json

from corki.protocol.wire_numbers import WireNumber

NUMBER_KEY = "$serde_json::private::Number"
RAW_VALUE_KEY = "$serde_json::private::RawValue"


class WireObject(dict):
    def __init__(self, pairs):
        self.pairs = tuple(pairs)
        super().__init__(self.pairs)


def _invalid_constant(value):
    raise ValueError("invalid JSON constant")


def loads_wire(value: str | bytes):
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if not isinstance(value, str):
        raise ValueError("JSON input must be text or UTF-8 bytes")
    try:
        return json.loads(
            value,
            object_pairs_hook=WireObject,
            parse_int=WireNumber,
            parse_float=WireNumber,
            parse_constant=_invalid_constant,
        )
    except RecursionError as error:
        raise ValueError("JSON nesting exceeds decoder limit") from error


def object_pairs(value):
    return value.pairs if isinstance(value, WireObject) else value.items()


def check_fields(value, fields):
    """Reject duplicates only for fields this typed struct actually reads."""
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    seen = set()
    for key, _ in object_pairs(value):
        if not isinstance(key, str):
            raise ValueError("JSON object key must be a string")
        key.encode("utf-8")  # Map keys are decoded even when their values are ignored.
        if key in fields:
            if key in seen:
                raise ValueError(f"duplicate field: {key}")
            seen.add(key)


def is_i64(value):
    # Typed integer decoding rejects floating syntax and negative zero,
    # independently of arbitrary-precision Value number representation.
    return (
        isinstance(value, WireNumber)
        and value.token != "-0"
        and len(value.token) <= 20
        and not any(char in value.token for char in ".eE")
        and -(2**63) <= int(value.token) < 2**63
    )


def _private_string(value, key):
    pairs = tuple(object_pairs(value))
    if len(pairs) != 1 or pairs[0][0] != key or not isinstance(pairs[0][1], str):
        raise ValueError("invalid private JSON value representation")
    return pairs[0][1]


def number_from_object(value):
    """Typed Number accepts its private map, not RawValue or arbitrary objects."""
    return WireNumber(_private_string(value, NUMBER_KEY)).value()


def validate_buffered_value(value):
    """Validate Value -> serde Content numeric visitors, not raw JSON numbers.

    Content has 64-bit visitors only. Number tries 128-bit visitors before its
    arbitrary-number map fallback, so only these intermediate integer ranges
    fail. Unknown fields are buffered too. Do not inspect JSON inside strings.
    """
    if type(value) is int and (2**64 <= value < 2**128 or -(2**127) <= value < -(2**63)):
        raise ValueError("128-bit JSON number has no buffered Content visitor")
    if isinstance(value, (dict, list)):
        for child in value.values() if isinstance(value, dict) else value:
            validate_buffered_value(child)


def materialize(value, *, depth=1, preserve_pairs=True):
    """Validate buffered/Value content; unknown fields outside it remain ignored."""
    if isinstance(value, WireNumber):
        return value.value()
    if isinstance(value, str):
        value.encode("utf-8")  # Reject unpaired escaped surrogates, including keys.
        return value
    if isinstance(value, (dict, list)):
        if depth >= 128:
            raise ValueError("JSON recursion limit exceeded")
        if isinstance(value, list):
            return [
                materialize(item, depth=depth + 1, preserve_pairs=preserve_pairs) for item in value
            ]
        if not preserve_pairs and value:
            first = next(iter(object_pairs(value)))[0]
            if first == NUMBER_KEY:
                return number_from_object(value)
            if first == RAW_VALUE_KEY:
                raw = _private_string(value, RAW_VALUE_KEY)
                # RawValue's string is parsed by a fresh serde_json deserializer.
                return materialize(loads_wire(raw), preserve_pairs=False)
        pairs = [
            (materialize(key), materialize(item, depth=depth + 1, preserve_pairs=preserve_pairs))
            for key, item in object_pairs(value)
        ]
        return WireObject(pairs) if preserve_pairs else dict(pairs)
    return value
