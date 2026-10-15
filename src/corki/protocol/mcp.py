"""Bounded MCP host-event JSON, separate from model-visible conversation items."""

from corki.protocol.wire_numbers import dumps_wire, loads_number_values

MCP_EVENT_PREVIEW_BYTES = 1024 * 1024
# A preview may escape every character again inside its containing JSON string.
MAX_MCP_EVENT_JSON_BYTES = 6 * (MCP_EVENT_PREVIEW_BYTES + 128) + 1024


def validate_mcp_event_json(value: str) -> None:
    """Validate immutable host data without reflecting its private contents."""
    try:
        if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_MCP_EVENT_JSON_BYTES:
            raise ValueError
        raw = loads_number_values(value)
        dumps_wire(raw).encode("utf-8")
        if not isinstance(raw, dict) or not isinstance(raw.get("content"), list):
            raise ValueError
        if raw.get("isError") is not None and type(raw["isError"]) is not bool:
            raise ValueError
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError("Invalid MCP host-event result JSON") from None


def validate_mcp_event_error(value: str) -> None:
    """A transport-error preview is bounded independently of serialized results."""
    try:
        if not isinstance(value, str) or len(value.encode("utf-8")) > MCP_EVENT_PREVIEW_BYTES + 128:
            raise ValueError
    except (ValueError, UnicodeError):
        raise ValueError("Invalid MCP host-event error") from None
