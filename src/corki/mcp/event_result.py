"""MCP event/ledger copy; private metadata never enters public tool projections."""

from collections.abc import Mapping
from typing import Any

from corki.context.hosted_output import truncate_output_text
from corki.protocol.mcp import MCP_EVENT_PREVIEW_BYTES
from corki.protocol.tools import CodeModeOutput
from corki.protocol.truncation import TruncationPolicy
from corki.protocol.wire_numbers import dumps_wire


def event_result_json(raw: Mapping[str, Any]) -> str:
    """Keep a small whole result, or preview an oversized serialized envelope."""
    value = {"content": raw.get("content", [])}
    for key in ("structuredContent", "isError", "_meta"):
        if raw.get(key) is not None:
            value[key] = raw[key]
    # Reuse the existing strict bounded JSON-value validator, not result text.
    value = CodeModeOutput(value).normalized().value
    encoded = dumps_wire(value)
    if len(encoded.encode("utf-8")) <= MCP_EVENT_PREVIEW_BYTES:
        return encoded
    preview = {
        "content": [
            {
                "type": "text",
                "text": truncate_output_text(
                    encoded, TruncationPolicy("bytes", MCP_EVENT_PREVIEW_BYTES)
                ),
            }
        ],
    }
    if value.get("isError") is not None:
        preview["isError"] = value["isError"]
    return dumps_wire(preview)
