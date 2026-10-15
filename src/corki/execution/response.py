"""One typed Result contract for host-owned native and filesystem helpers.

Decoding does not retry the helper or decide whether its side effects completed.
Consumers retain their own startup, observation and publication failure semantics.
"""

import json
import math

from corki.protocol.wire_json import WireObject, check_fields, materialize


def _object(pairs):
    value = WireObject(pairs)
    check_fields(value, set(value))
    return value


def _finite_number(token):
    value = float(token)
    if not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    return value


def decode_helper_response(output: bytes, *, expected: type, error_prefix: str):
    """Reject ambiguous results before a consumer can use any success payload."""
    try:
        envelope = materialize(
            json.loads(
                output,
                object_pairs_hook=_object,
                parse_float=_finite_number,
                parse_constant=_finite_number,
            )
        )
        if not isinstance(envelope, dict) or set(envelope) not in ({"ok"}, {"error"}):
            raise ValueError("expected exactly one ok or error field")
        if "error" in envelope:
            if not isinstance(envelope["error"], str):
                raise ValueError("helper error must be a string")
        elif not isinstance(envelope["ok"], expected):
            raise ValueError(f"helper success must be {expected.__name__}")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ValueError(f"{error_prefix}: invalid response ({str(exc)[:500]})") from exc
    if "error" in envelope:
        raise ValueError(f"{error_prefix}: {envelope['error'][:2000]}")
    return envelope["ok"]
