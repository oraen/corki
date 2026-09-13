"""RMCP JSON Value fields, distinct from typed structs and metadata map roots."""

from corki.mcp.json_rpc import MCPProtocolError, plain_json
from corki.protocol.wire_json import WireObject, materialize, object_pairs
from corki.protocol.wire_numbers import WireNumber


def json_value(value):
    try:
        if isinstance(value, WireObject):
            return materialize(value, preserve_pairs=False)
        if isinstance(value, WireNumber):
            return value.value()
        if isinstance(value, list):
            return [json_value(item) for item in value]
        # Already-projected or trusted typed values must not interpret marker
        # keys again on repeated validation or after a durable result reload.
        return plain_json(value)
    except (ValueError, RecursionError):
        raise MCPProtocolError("MCP result contains an invalid JSON value") from None


def metadata_map(value):
    # MetaObject is Map<String, Value>, not Value at the root. Process every
    # occurrence before last-key-wins so invalid earlier values cannot disappear.
    return {key: json_value(item) for key, item in object_pairs(value)}
