"""Remote MCP arguments are JSON objects, not instances of the advertised schema."""

from copy import deepcopy

from corki.config.mcp_headers import RUST_WHITESPACE
from corki.mcp.client import MCPProtocolError
from corki.protocol.wire_json import loads_wire, materialize
from corki.protocol.wire_numbers import dumps_wire


def hook_arguments(call):
    """Local Hook projection accepts any JSON value and preserves malformed text."""
    raw = call.raw_arguments
    if not raw:
        return deepcopy(dict(call.arguments)) if call.arguments is not None else {}
    if not raw.strip(RUST_WHITESPACE):
        return {}
    try:
        return materialize(loads_wire(raw), preserve_pairs=False)
    except (ValueError, RecursionError):
        return raw


def call_arguments(call):
    if call.raw_arguments:
        if not call.raw_arguments.strip(RUST_WHITESPACE):
            return None
        try:
            # This is a serde Value boundary, unlike the typed JSON-RPC envelope.
            # Do not round business numbers or apply Value -> Content restrictions.
            value = materialize(loads_wire(call.raw_arguments), preserve_pairs=False)
        except (ValueError, RecursionError) as error:
            raise MCPProtocolError(f"invalid JSON arguments: {error}") from error
    else:
        # Scripted/provider-neutral callers can supply a decoded object without
        # a raw spelling. A genuinely absent object remains absent on MCP wire.
        value = deepcopy(dict(call.arguments)) if call.arguments is not None else None
        if value is None:
            return None
    if not isinstance(value, dict):
        raise MCPProtocolError("MCP tool arguments must be a JSON object")
    try:
        dumps_wire(value).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise MCPProtocolError(f"invalid JSON arguments: {error}") from error
    return value
