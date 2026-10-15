"""Pair positional classifications with content during host-owned projection."""

from corki.protocol.response_items import METADATA
from corki.protocol.tools import AudioAttachment, ImageAttachment


def normalized_kinds(kinds, count):
    return [kinds[i] if kinds and i < len(kinds) else "unknown" for i in range(count)]


def user_content_kinds(item, parts):
    if item.content_item_kinds is not None:
        return normalized_kinds(item.content_item_kinds, len(parts))
    return [
        "user.image"
        if isinstance(p, ImageAttachment)
        else "user.audio"
        if isinstance(p, AudioAttachment)
        else "user.text"
        for p in parts
    ]


def project_message_media(payload, replacements):
    """Replace content and kind together on a copy; legacy missing kinds are unknown."""
    if not replacements or payload.get("type", "message") != "message":
        return payload
    content = payload.get("content")
    if not isinstance(content, list):
        return payload
    metadata = dict(payload.get(METADATA) or {})
    kinds = normalized_kinds(metadata.get("content_item_kinds"), len(content))
    projected = []
    for index, part in enumerate(content):
        if part["type"] in replacements:
            kinds[index], text = replacements[part["type"]]
            part = {"type": "input_text", "text": text}
        projected.append(part)
    metadata["content_item_kinds"] = kinds
    return {**payload, "content": projected, METADATA: metadata}
