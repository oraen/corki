"""Provider-neutral extraction archive, distinct from the active model window.

Raw durable items keep original tool identities and observations even after
compaction. Context snapshots and explicitly marked retained copies are not
fresh evidence. This is not a reconstruction of a provider's native wire log.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from corki.protocol.items import (
    AssistantMessageItem,
    ConversationItem,
    HostedToolItem,
    ToolCallItem,
    ToolResultItem,
    TurnAbortedItem,
    UserMessageItem,
    item_kind,
    item_to_payload,
)
from corki.protocol.tools import TextContent


def render_transcript(items: tuple[ConversationItem, ...], *, redact: Callable[[str], str]) -> str:
    rows = []
    for item in items:
        if isinstance(item, UserMessageItem):
            if item.retained_from_id is not None:
                continue
            if item.content_items:
                parts = tuple(
                    part
                    for part in item.content_items
                    if not (isinstance(part, TextContent) and _excluded_fragment(part.text))
                )
                if not parts:
                    continue
                item = replace(item, content_items=parts)
            elif _excluded_fragment(item.content):
                if not item.attachments:
                    continue
                item = replace(item, content="")
        elif not isinstance(
            item,
            (AssistantMessageItem, ToolCallItem, ToolResultItem, TurnAbortedItem, HostedToolItem),
        ):
            continue

        row = {"type": item_kind(item), **item_to_payload(item)}
        if isinstance(item, HostedToolItem):
            row.pop("model_payload_json", None)
        if isinstance(item, (UserMessageItem, ToolResultItem)) and item.content_items:
            # Authoritative ordered content supersedes legacy fallback fields.
            # Keeping them would duplicate media or reintroduce filtered text.
            row.pop("content")
            row.pop("attachments")
        if isinstance(item, ToolResultItem):
            row.pop("display_content")
            row.pop("state_update")
        rows.append(row)
    # Redact before encoding: a regexp must never remove half of a JSON escape.
    return json.dumps(_redact_values(rows, redact), ensure_ascii=False, separators=(",", ":"))


def _excluded_fragment(text: str) -> bool:
    text = text.strip()
    for start, end in (
        ("# AGENTS.md instructions", "</INSTRUCTIONS>"),
        ("<skill>", "</skill>"),
    ):
        prefix, suffix = text[: len(start)], text[-len(end) :]
        if (
            prefix.isascii()
            and suffix.isascii()
            and prefix.lower() == start.lower()
            and suffix.lower() == end.lower()
        ):
            return True
    return False


def _redact_values(value: Any, redact: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {
            redact(key) if isinstance(key, str) else key: _redact_values(part, redact)
            for key, part in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_values(part, redact) for part in value]
    return value
