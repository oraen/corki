"""Conservative provider-neutral token estimates used before sampling."""

from __future__ import annotations

import base64
import json
import math
from collections.abc import Iterable

from corki.protocol.audio import wav_duration_seconds
from corki.protocol.items import (
    AssistantMessageItem,
    BudgetNoticeItem,
    CompactionItem,
    ContextItem,
    ConversationItem,
    HostedToolItem,
    ReasoningItem,
    ToolCallItem,
    ToolResultItem,
    TurnAbortedItem,
    UserMessageItem,
)
from corki.protocol.tool_names import compatible_tool_name
from corki.protocol.tools import (
    AudioAttachment,
    EncryptedContent,
    ImageAttachment,
    TextContent,
    ToolSpec,
)

_RESIZED_IMAGE_TOKENS = math.ceil(7_373 / 4)
_ORIGINAL_IMAGE_PATCH_SIZE = 32
_ORIGINAL_IMAGE_MAX_PATCHES = 10_000
_IMAGE_HEADER_DECODE_LIMIT = 512 * 1_024


def estimate_text_tokens(text: str) -> int:
    """Approximate code compactly while keeping Unicode estimates conservative."""

    if not text:
        return 0
    ascii_chars = sum(character.isascii() for character in text)
    non_ascii_bytes = len(text.encode("utf-8")) - ascii_chars
    # Codex uses roughly four serialized bytes per token. Non-ASCII scripts and
    # emoji tokenize less predictably, so reserve one token per two UTF-8 bytes.
    return max(1, math.ceil(ascii_chars / 4 + non_ascii_bytes / 2))


def estimate_item_tokens(item: ConversationItem) -> int:
    if isinstance(item, ContextItem) and item.is_snapshot_only:
        return 0
    overhead = 12
    if isinstance(item, HostedToolItem):
        return overhead + estimate_text_tokens(item.compatibility_content)
    if isinstance(item, UserMessageItem):
        if item.content_items:
            return overhead + sum(
                estimate_text_tokens(part.text)
                if isinstance(part, TextContent)
                else estimate_audio_tokens(part)
                if isinstance(part, AudioAttachment)
                else _estimate_image_tokens(part)
                for part in item.content_items
            )
        return (
            overhead
            + estimate_text_tokens(item.content)
            + sum(_estimate_image_tokens(value) for value in item.attachments)
        )
    if isinstance(item, ReasoningItem):
        if item.encrypted_content is not None:
            # Codex estimates decoded model-visible reasoning after its fixed
            # encrypted envelope, not the serialized base64/summary text.
            decoded_bytes = max(0, len(item.encrypted_content.encode("utf-8")) * 3 // 4 - 650)
            return (decoded_bytes + 3) // 4
        return (
            overhead
            + estimate_text_tokens(item.content)
            + estimate_text_tokens(item.encrypted_content or "")
        )
    if isinstance(item, (AssistantMessageItem, ContextItem, TurnAbortedItem, BudgetNoticeItem)):
        return overhead + estimate_text_tokens(item.content)
    if isinstance(item, ToolCallItem):
        serialized = item.call.raw_arguments or json.dumps(
            dict(item.call.arguments or {}), ensure_ascii=False
        )
        if item.call.input_kind == "freeform" and item.call.parse_error is None:
            # The compatible wrapper escapes source newlines/quotes. Reserve
            # that representation even when native transport may be cheaper.
            serialized = json.dumps({"input": item.call.raw_arguments}, ensure_ascii=False)
        name_tokens = max(
            estimate_text_tokens(item.call.name),
            estimate_text_tokens(compatible_tool_name(item.call.name)),
        )
        return overhead + name_tokens + estimate_text_tokens(serialized)
    if isinstance(item, ToolResultItem):
        if item.content_items:
            return (
                overhead
                + estimate_text_tokens(item.tool_name)
                + sum(
                    estimate_text_tokens(part.text)
                    if isinstance(part, TextContent)
                    else estimate_audio_tokens(part)
                    if isinstance(part, AudioAttachment)
                    else estimate_encrypted_output_tokens(
                        part, discounted=item.input_kind != "freeform"
                    )
                    if isinstance(part, EncryptedContent)
                    else _estimate_image_tokens(part)
                    for part in item.content_items
                )
            )
        content_tokens = estimate_text_tokens(item.content)
        if item.discovered_tools:
            # Native search serializes the typed definitions rather than the
            # display text. A short content string must not hide schema costs.
            content_tokens = max(
                content_tokens,
                64
                + estimate_text_tokens(
                    json.dumps(
                        [spec.as_chat_completion_tool() for spec in item.discovered_tools],
                        ensure_ascii=False,
                    )
                ),
                64 + sum(estimate_tool_tokens(spec) for spec in item.discovered_tools),
            )
        return (
            overhead
            + estimate_text_tokens(item.tool_name)
            + content_tokens
            + sum(_estimate_image_tokens(value) for value in item.attachments)
        )
    if isinstance(item, CompactionItem):
        if item.context_reset:
            return 0
        return overhead + estimate_text_tokens(item.summary)
    raise TypeError(f"unsupported conversation item: {type(item).__name__}")


def estimate_encrypted_output_tokens(part: EncryptedContent, *, discounted: bool = True) -> int:
    encoded_bytes = len(part.encrypted_content.encode("utf-8"))
    visible_bytes = (encoded_bytes * 9 + 15) // 16 if discounted else encoded_bytes
    return (visible_bytes + 3) // 4


def estimate_audio_tokens(attachment: AudioAttachment) -> int:
    duration = wav_duration_seconds(attachment.data_url)
    return (
        math.ceil(duration * 10)
        if duration is not None
        else estimate_text_tokens(attachment.data_url)
    )


def _estimate_image_tokens(attachment: ImageAttachment) -> int:
    if attachment.detail != "original":
        return _RESIZED_IMAGE_TOKENS
    dimensions = _image_dimensions(attachment.data_url)
    if dimensions is None:
        return _RESIZED_IMAGE_TOKENS
    width, height = dimensions
    if width <= 0 or height <= 0:
        return _RESIZED_IMAGE_TOKENS
    patches = math.ceil(width / _ORIGINAL_IMAGE_PATCH_SIZE) * math.ceil(
        height / _ORIGINAL_IMAGE_PATCH_SIZE
    )
    return min(patches, _ORIGINAL_IMAGE_MAX_PATCHES)


def _image_dimensions(data_url: str) -> tuple[int, int] | None:
    """Read common image dimensions from a bounded data-URL prefix."""

    metadata, separator, payload = data_url.partition(",")
    if not separator or not metadata.casefold().startswith("data:image/"):
        return None
    if ";base64" not in metadata.casefold():
        return None
    encoded = payload[: math.ceil(_IMAGE_HEADER_DECODE_LIMIT / 3) * 4]
    try:
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), validate=False)
    except (ValueError, TypeError):
        return None
    if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) >= 24:
        return int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
    if raw[:3] in {b"GIF", b"gif"} and len(raw) >= 10:
        return int.from_bytes(raw[6:8], "little"), int.from_bytes(raw[8:10], "little")
    if raw.startswith(b"\xff\xd8"):
        return _jpeg_dimensions(raw)
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return _webp_dimensions(raw)
    return None


def _jpeg_dimensions(raw: bytes) -> tuple[int, int] | None:
    position = 2
    start_of_frame = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while position + 4 <= len(raw):
        if raw[position] != 0xFF:
            position += 1
            continue
        while position < len(raw) and raw[position] == 0xFF:
            position += 1
        if position >= len(raw):
            return None
        marker = raw[position]
        position += 1
        if marker in {0xD8, 0xD9, 0x01, *range(0xD0, 0xD8)}:
            continue
        if position + 2 > len(raw):
            return None
        length = int.from_bytes(raw[position : position + 2], "big")
        if length < 2 or position + length > len(raw):
            return None
        if marker in start_of_frame and length >= 7:
            height = int.from_bytes(raw[position + 3 : position + 5], "big")
            width = int.from_bytes(raw[position + 5 : position + 7], "big")
            return (width, height) if width and height else None
        position += length
    return None


def _webp_dimensions(raw: bytes) -> tuple[int, int] | None:
    if len(raw) < 30:
        return None
    kind = raw[12:16]
    if kind == b"VP8X":
        width = 1 + int.from_bytes(raw[24:27], "little")
        height = 1 + int.from_bytes(raw[27:30], "little")
        return width, height
    if kind == b"VP8 " and len(raw) >= 30 and raw[23:26] == b"\x9d\x01\x2a":
        return (
            int.from_bytes(raw[26:28], "little") & 0x3FFF,
            int.from_bytes(raw[28:30], "little") & 0x3FFF,
        )
    if kind == b"VP8L" and len(raw) >= 25 and raw[20] == 0x2F:
        bits = int.from_bytes(raw[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None


def estimate_request_tokens(
    instructions: str,
    items: Iterable[ConversationItem],
    tools: Iterable[ToolSpec],
) -> int:
    tool_tokens = sum(estimate_tool_tokens(tool) for tool in tools)
    return (
        estimate_text_tokens(instructions)
        + tool_tokens
        + sum(estimate_item_tokens(item) for item in items)
    )


def estimate_tool_tokens(tool: ToolSpec) -> int:
    """Reserve the larger wire representation, including native raw grammars."""
    return max(
        estimate_text_tokens(json.dumps(definition, ensure_ascii=False, sort_keys=True))
        for definition in (
            tool.as_chat_completion_tool(),
            tool.as_response_tool(native_freeform=True),
        )
    )
