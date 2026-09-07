"""Model-only function output copies, independent of the execution ledger."""

from dataclasses import replace

from corki.context.hosted_output import truncate_output_body, truncate_output_text
from corki.context.tool_output import truncate_content
from corki.context.truncation import truncate_text
from corki.protocol.items import ConversationItem, ToolResultItem
from corki.protocol.tools import (
    AudioAttachment,
    EncryptedContent,
    ImageAttachment,
    TextContent,
    ToolContent,
    content_text,
)
from corki.protocol.truncation import TruncationPolicy


def truncate_function_content(
    parts: tuple[ToolContent, ...], policy: TruncationPolicy
) -> tuple[ToolContent, ...]:
    """Use the same ordered byte/token policy as native hosted function output."""
    native = []
    for part in parts:
        if isinstance(part, TextContent):
            native.append({"type": "input_text", "text": part.text})
        elif isinstance(part, ImageAttachment):
            native.append(
                {"type": "input_image", "image_url": part.data_url, "detail": part.detail}
            )
        elif isinstance(part, AudioAttachment):
            native.append({"type": "input_audio", "audio_url": part.data_url})
        elif isinstance(part, EncryptedContent):
            native.append(
                {"type": "encrypted_content", "encrypted_content": part.encrypted_content}
            )
        else:
            raise ValueError("invalid function output content item")
    projected: list[ToolContent] = []
    for part in truncate_output_body(native, policy):
        if part["type"] == "input_text":
            projected.append(TextContent(part["text"]))
        elif part["type"] == "input_image":
            projected.append(ImageAttachment(part["image_url"], part["detail"]))
        elif part["type"] == "input_audio":
            projected.append(AudioAttachment(part["audio_url"]))
        else:
            projected.append(EncryptedContent(part["encrypted_content"]))
    return tuple(projected)


def project_function_items(
    items: tuple[ConversationItem, ...], policy: TruncationPolicy
) -> tuple[ConversationItem, ...]:
    """Freeze request copies; leave durable originals and already frozen copies intact."""
    output: list[ConversationItem] = []
    for item in items:
        if not isinstance(item, ToolResultItem) or item.model_output_projected:
            output.append(item)
            continue
        # Legacy histories had no typed output discriminator. Preserve their
        # search definitions, but never infer new outputs from a handler name.
        search = item.is_tool_search_output
        if search is None:
            search = bool(item.discovered_tools) or item.tool_name == "tool_search"
        if search:
            output.append(item)
            continue
        effective = (
            TruncationPolicy("tokens", item.fallback_token_limit_override)
            if item.fallback_token_limit_override is not None
            else policy.history_allowance()
        )
        parts = item.content_items
        if parts:
            parts = truncate_function_content(parts, effective)
            if item.legacy_output_char_budget is not None:
                parts = truncate_content(parts, item.legacy_output_char_budget // 4)
            content = content_text(parts)
        else:
            content = truncate_output_text(item.content, effective)
            if item.legacy_output_char_budget is not None:
                content = truncate_text(content, item.legacy_output_char_budget)
        output.append(
            item
            if content == item.content and parts == item.content_items
            else replace(item, content=content, content_items=parts, model_output_projected=True)
        )
    return tuple(output)
