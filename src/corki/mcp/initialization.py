"""Legacy initialization contracts from the pinned RMCP message/result types."""

from dataclasses import dataclass
from typing import Any

from corki.config.mcp_headers import RUST_WHITESPACE
from corki.mcp.json_rpc import MCPProtocolError, decode_json, plain_json
from corki.mcp.json_values import metadata_map
from corki.mcp.result_candidates import _implementation, initialization_views
from corki.mcp.sse import SseEvent


@dataclass(frozen=True, slots=True)
class Initialization:
    """Validated fields consumed by the local legacy client, not remote authority."""

    protocol_version: str
    instructions: str | None
    server_info: dict
    tool_catalog_cacheable: bool = True


def validate_initialization(value: object) -> Initialization:
    """Select the typed result before notifications or catalog publication."""
    try:
        result, capabilities = initialization_views(value)
        cache = metadata_map(
            (capabilities.get("experimental") or {}).get("codex/tool-catalog-cache", {})
        )
    except MCPProtocolError:
        # Candidate errors must not reflect private remote values into warnings.
        raise MCPProtocolError("Invalid MCP initialize result") from None
    return Initialization(
        result["protocolVersion"],
        result.get("instructions"),
        plain_json(_implementation(result["serverInfo"])),
        cache.get("cacheable") is not False,
    )


def handshake_message(value: object) -> dict[str, Any] | None:
    """Classify initialization response/error, preserving first-response correlation."""
    if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
        raise MCPProtocolError("Invalid initialize JSON-RPC envelope")
    # Requests/notifications precede errors in RMCP's untagged message enum.
    if isinstance(value.get("method"), str):
        return None
    identity = value.get("id")
    valid_id = (
        isinstance(identity, str) or type(identity) is int and -(1 << 63) <= identity < 1 << 63
    )
    # Extra fields are ignored; a valid Response wins over an extra error field.
    if "result" in value and valid_id:
        return {"jsonrpc": "2.0", "id": identity, "result": value["result"]}
    error = value.get("error")
    if isinstance(error, dict) and (identity is None or valid_id):
        code = error.get("code")
        if (
            type(code) is int
            and -(1 << 31) <= code < 1 << 31
            and isinstance(error.get("message"), str)
        ):
            return {"jsonrpc": "2.0", "id": identity, "error": error}
    raise MCPProtocolError("Invalid initialize JSON-RPC response")


def initialization_event(event: SseEvent) -> dict[str, Any] | None:
    """HTTP initialize ignores event type, but cannot skip malformed nonempty JSON."""
    if event.data is None or not event.data.strip(RUST_WHITESPACE):
        return None
    message = handshake_message(decode_json(event.data))
    return message if message is not None and "result" in message else None
