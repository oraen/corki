"""Shared JSON decoding and protocol errors for MCP transport boundaries."""

import json
import re
from copy import deepcopy
from typing import Any

from corki.protocol.wire_json import WireObject, check_fields, object_pairs
from corki.protocol.wire_numbers import WireNumber


class MCPProtocolError(RuntimeError):
    """A remote response cannot satisfy the MCP request contract."""


def check_known_fields(value, fields):
    """Only typed fields reject duplicates; JSON Value and ignored fields do not."""
    try:
        check_fields(value if isinstance(value, dict) else dict(value), fields)
    except ValueError:
        raise MCPProtocolError("MCP result contains duplicate or invalid fields") from None


def plain_json(value):
    """Leave the parsing representation at a JSON Value/public result boundary."""
    memo = {}

    def copy(item):
        if isinstance(item, WireNumber):
            return item.value()
        identity = id(item)
        if identity in memo:
            return memo[identity]
        if type(item) is dict or isinstance(item, WireObject):
            result = memo[identity] = {}
            result.update((key, copy(child)) for key, child in item.items())
            return result
        if type(item) is list:
            result = memo[identity] = []
            result.extend(copy(child) for child in item)
            return result
        return deepcopy(item, memo)

    return copy(value)


def _decoded_object(pairs):
    # Retain raw Value/map role until the typed field chooses its deserializer.
    return WireObject(pairs)


def _integer(token):
    if len(token) <= 20:
        value = int(token)
        if -(2**63) <= value < 2**64:
            return value
    return WireNumber(token)


def _check_strings(value):
    # Serde buffers every occurrence, including values hidden by later map keys.
    if isinstance(value, str):
        value.encode("utf-8")
    elif isinstance(value, dict):
        for key, child in object_pairs(value):
            _check_strings(key)
            _check_strings(child)
    elif isinstance(value, list):
        for child in value:
            _check_strings(child)


def _invalid_constant(value):
    raise ValueError("invalid JSON constant")


def integer_response_id(value: object) -> int | None:
    """RMCP falls back from an ASCII i64 string to a pending integer identity."""
    if type(value) is int:
        return value if -(1 << 63) <= value < (1 << 63) else None
    if not isinstance(value, str) or re.fullmatch(r"[+-]?[0-9]+", value) is None:
        return None
    digits = value.lstrip("+-").lstrip("0") or "0"
    if len(digits) > 19:
        return None
    parsed = int(digits) * (-1 if value.startswith("-") else 1)
    return parsed if -(1 << 63) <= parsed < (1 << 63) else None


def decode_json(data: bytes | str) -> Any:
    """Reject Python-only JSON values without reflecting untrusted response data."""
    try:
        value = json.loads(
            data.decode("utf-8") if isinstance(data, bytes) else data,
            object_pairs_hook=_decoded_object,
            parse_constant=_invalid_constant,
            parse_int=_integer,
            parse_float=WireNumber,
        )
        _check_strings(value)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise MCPProtocolError("MCP response contains invalid JSON") from error
    return value
