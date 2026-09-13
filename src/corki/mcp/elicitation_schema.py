"""Normalize the pinned RMCP elicitation schema wire types, not arbitrary JSON Schema."""

import math
from functools import partial

from corki.mcp.json_rpc import MCPProtocolError
from corki.mcp.wire_types import enum, struct_object
from corki.protocol.wire_json import WireObject, check_fields, object_pairs


def _string(value):
    if not isinstance(value, str):
        raise ValueError("Expected a string")
    return value


def _boolean(value):
    if type(value) is not bool:
        raise ValueError("Expected a boolean")
    return value


def _integer(value, *, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("Integer is outside its wire type")
    return value


_I64 = partial(_integer, minimum=-(1 << 63), maximum=(1 << 63) - 1)
_U32 = partial(_integer, minimum=0, maximum=(1 << 32) - 1)
_U64 = partial(_integer, minimum=0, maximum=(1 << 64) - 1)


def _number(value):
    if type(value) not in (int, float):
        raise ValueError("Expected a number")
    try:
        value = float(value)
    except OverflowError as error:
        raise ValueError("Number exceeds f64") from error
    if not math.isfinite(value):
        raise ValueError("Expected a finite number")
    return value


def _constant(expected, value):
    if _string(value) != expected:
        raise ValueError("Unexpected schema type")
    return value


def _array(convert, value):
    if not isinstance(value, list):
        raise ValueError("Expected an array")
    return [convert(item) for item in value]


_STRINGS = partial(_array, _string)
_COMMON = {"title": _string, "description": _string}


def _record(value, fields, *, required=(), reject_unknown=False, sequence=None):
    # These schema records are ordinary, nonflattened structs. Their final
    # fields have no missing-slot default, even when they are Option<T>.
    try:
        value = struct_object(value, tuple(fields) if sequence is None else sequence)
    except MCPProtocolError:
        raise ValueError("Invalid schema sequence length") from None
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("Expected an object")
    check_fields(value, fields)
    if any(key not in value for key in required):
        raise ValueError("Missing required schema field")
    if reject_unknown and value.keys() - fields.keys():
        raise ValueError("Unknown field in strict enum variant")
    result = {}
    for key, convert in fields.items():
        if key in required or value.get(key) is not None:
            result[key] = convert(value.get(key))
    return result


def _typed(value, kind, fields, *, required=(), reject_unknown=False):
    return _record(
        value,
        {"type": partial(_constant, kind), **_COMMON, **fields},
        required=("type", *required),
        reject_unknown=reject_unknown,
    )


def _const_title(value):
    return _record(value, {"const": _string, "title": _string}, required=("const", "title"))


_CHOICES = partial(_array, _const_title)


def _untitled_items(value):
    return _record(
        value,
        {"type": partial(_constant, "string"), "enum": _STRINGS},
        required=("type", "enum"),
    )


def _titled_items(value):
    if isinstance(value, dict):
        value = WireObject(
            ("anyOf" if key == "oneOf" else key, item) for key, item in object_pairs(value)
        )
    return _record(value, {"anyOf": _CHOICES}, required=("anyOf",))


_string_format = enum("email", "uri", "date", "date-time")


def _format(value):
    try:
        return _string_format(value)
    except MCPProtocolError:
        # Keep schema candidate fallback and host -32602 handling at ValueError.
        raise ValueError("Unsupported elicitation string format") from None


def _property(value):
    # Serde's untagged enums select the first fully matching variant. An invalid
    # enum field can fall through to StringSchema, where that field is unknown.
    variants = (
        ("string", {"enum": _STRINGS, "default": _string}, ("enum",), True),
        ("string", {"oneOf": _CHOICES, "default": _string}, ("oneOf",), False),
        (
            "array",
            {"minItems": _U64, "maxItems": _U64, "items": _untitled_items, "default": _STRINGS},
            ("items",),
            False,
        ),
        (
            "array",
            {"minItems": _U64, "maxItems": _U64, "items": _titled_items, "default": _STRINGS},
            ("items",),
            False,
        ),
        ("string", {"enum": _STRINGS, "enumNames": _STRINGS, "default": _string}, ("enum",), False),
        (
            "string",
            {"minLength": _U32, "maxLength": _U32, "format": _format, "default": _string},
            (),
            False,
        ),
        ("number", {"minimum": _number, "maximum": _number, "default": _number}, (), False),
        ("integer", {"minimum": _I64, "maximum": _I64, "default": _I64}, (), False),
        ("boolean", {"default": _boolean}, (), False),
    )
    for kind, fields, required, strict in variants:
        try:
            return _typed(value, kind, fields, required=required, reject_unknown=strict)
        except ValueError:
            continue
    raise ValueError("Invalid MCP elicitation property schema")


def _properties(value):
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError("Expected a property map")
    # Unlike RMCP's internal BTreeMap, its wire property_order preserves input order.
    return {key: _property(field) for key, field in object_pairs(value)}


def normalize_schema(value):
    """Decode/re-encode supported wire fields, without builder-only semantic checks."""
    return _record(
        value,
        {
            "$schema": _string,
            "type": partial(_constant, "object"),
            **_COMMON,
            "properties": _properties,
            "required": _STRINGS,
        },
        required=("type", "properties"),
        sequence=("$schema", "type", "title", "properties", "required", "description"),
    )
