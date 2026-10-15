"""Shared typed checks over raw RMCP maps, without erasing field occurrences."""

from corki.mcp.json_rpc import MCPProtocolError, check_known_fields
from corki.mcp.json_values import metadata_map
from corki.protocol.wire_json import WireObject, object_pairs


def invalid():
    raise MCPProtocolError("Invalid MCP result candidate")


def object_value(value):
    if not isinstance(value, dict):
        invalid()
    return value


def text(value):
    if not isinstance(value, str):
        invalid()


def boolean(value):
    if type(value) is not bool:
        invalid()


def integer(low, high):
    def check(value):
        # Raw decimals/exponents/out-of-u64 integers are buffered maps.
        if type(value) is not int or not low <= value < high:
            invalid()

    return check


i64 = integer(-(2**63), 2**63)
u64 = integer(0, 2**64)
u32 = integer(0, 2**32)


def literal(*choices):
    def check(value):
        if not isinstance(value, str) or value not in choices:
            invalid()
        return value

    return check


def enum(*choices):
    def check(value):
        if isinstance(value, dict):
            pairs = tuple(object_pairs(value))
            if len(pairs) != 1 or pairs[0][1] is not None:
                invalid()
            value = pairs[0][0]
        return literal(*choices)(value)

    return check


def vector(check):
    def validate(value):
        if not isinstance(value, list):
            invalid()
        for item in value:
            check(item)

    return validate


def mapping(check):
    def validate(value):
        for key, item in object_pairs(object_value(value)):
            text(key)
            check(item)

    return validate


def metadata(value):
    return metadata_map(object_value(value))


def struct_object(value, fields):
    """View an audited fixed-arity struct sequence without erasing nested pairs.

    All callers have a final field without a serde missing-value default. An
    optional field still needs its sequence slot; only map absence implies None.
    Map-only/flattened types must not use this conversion.
    """
    if isinstance(value, list):
        if len(value) != len(fields):
            invalid()
        return WireObject(zip(fields, value, strict=True))
    return value


def shape(required=None, optional=None, *, sequence=None):
    required, optional = required or {}, optional or {}

    def validate(value):
        if sequence is not None:
            value = struct_object(value, sequence)
        object_value(value)
        check_known_fields(value, required.keys() | optional.keys())
        for key, check in required.items():
            if key not in value:
                invalid()
            check(value[key])
        for key, check in optional.items():
            if value.get(key) is not None:
                check(value[key])
        return value

    return validate
