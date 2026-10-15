"""MCP-specific public result, ordered model payload and text-only log projections."""

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from corki.context.function_output import truncate_function_content
from corki.context.hosted_output import truncate_output_text
from corki.mcp.client import validate_tool_result
from corki.mcp.event_result import event_result_json
from corki.protocol.mcp import MCP_EVENT_PREVIEW_BYTES
from corki.protocol.tools import (
    AudioAttachment,
    CodeModeOutput,
    EncryptedContent,
    ImageAttachment,
    TextContent,
    ToolCall,
    ToolContent,
    ToolResult,
    content_text,
)
from corki.protocol.truncation import TruncationPolicy
from corki.protocol.wire_numbers import dumps_wire
from corki.tools.base import ToolContext


def _json(value: object) -> str:
    return dumps_wire(value, sort_keys=True)


def _convert(item: object) -> ToolContent:
    if isinstance(item, Mapping):
        kind = item.get("type")
        meta = item.get("_meta")
        meta = meta if isinstance(meta, Mapping) else {}
        if kind == "text" and isinstance(item.get("text"), str):
            return (
                EncryptedContent(item["text"])
                if meta.get("codex/encryptedContent") is True
                else TextContent(item["text"])
            )
        data = item.get("data")
        mime = item.get("mimeType", item.get("mime_type"))
        if (
            isinstance(kind, str)
            and kind in {"image", "audio"}
            and isinstance(data, str)
            and (mime is None or isinstance(mime, str))
            and not ("mimeType" in item and "mime_type" in item)
        ):
            url = (
                data
                if data.startswith("data:")
                else "data:"
                + (mime if mime is not None else "application/octet-stream")
                + f";base64,{data}"
            )
            if kind == "audio":
                return AudioAttachment(url)
            detail = meta.get("codex/imageDetail")
            if not isinstance(detail, str) or detail not in {"auto", "low", "high", "original"}:
                detail = "high"
            return ImageAttachment(url, detail)
    return TextContent(_json(item))


def mcp_output(
    call: ToolCall,
    raw: Mapping[str, Any],
    *,
    context: ToolContext,
    policy: TruncationPolicy,
    wall_time: float,
    transport_error: str | None = None,
) -> ToolResult:
    """Convert one admitted result; nested values and logs precede model truncation."""
    raw = validate_tool_result(raw)
    content = []
    for item in raw.get("content", []):
        kind = item.get("type") if isinstance(item, Mapping) else None
        if kind == "image" and not context.supports_image_input:
            item = {
                "type": "text",
                "text": "<image content omitted because you do not support image input>",
            }
        elif kind == "audio" and not context.supports_audio_input:
            item = {
                "type": "text",
                "text": "<audio content omitted because you do not support audio input>",
            }
        content.append(item)
    public = {"content": content}
    for key in ("structuredContent", "isError"):
        if raw.get(key) is not None:
            public[key] = raw[key]
    # Validate/copy the bounded public protocol value, never private transport metadata.
    nested = CodeModeOutput(public).normalized()
    public = nested.value
    parts = tuple(_convert(item) for item in public["content"])
    structured = public.get("structuredContent")
    is_structured = structured is not None and not any(
        isinstance(p, EncryptedContent) for p in parts
    )
    header = f"Wall time: {wall_time:.4f} seconds\nOutput:"
    log_body = (
        _json(structured)
        if is_structured
        else "\n".join(
            part.text for part in parts if isinstance(part, TextContent) and part.text.strip()
        )
    )
    display = header + ("\n" + log_body if log_body else "")
    allowance = policy.history_allowance()
    if is_structured:
        text = truncate_output_text(header + "\n" + _json(structured), allowance)
        parts = ()
    else:
        parts = tuple(
            replace(p, detail="high")
            if isinstance(p, ImageAttachment)
            and p.detail == "original"
            and not context.supports_image_detail_original
            else p
            for p in parts
        )
        parts = truncate_function_content((TextContent(header), *parts), allowance)
        text = content_text(parts)
    return ToolResult(
        call.id,
        call.name,
        text,
        is_error=raw.get("isError") is True,
        display_content=display,
        content_items=parts,
        code_mode_output=nested,
        mcp_result_json=event_result_json(raw) if transport_error is None else None,
        mcp_error=truncate_output_text(
            transport_error, TruncationPolicy("bytes", MCP_EVENT_PREVIEW_BYTES)
        )
        if transport_error is not None
        else None,
        fallback_token_limit_override=(allowance.limit + 3) // 4
        if allowance.mode == "bytes"
        else allowance.limit,
    )
