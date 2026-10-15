"""Conversation-history normalization and compaction views."""

from __future__ import annotations

from uuid import UUID, uuid5

from corki.context.world_state import render_context_history
from corki.protocol.ids import ItemId, ToolCallId
from corki.protocol.items import (
    CompactionItem,
    ConversationItem,
    ToolCallItem,
    ToolResultItem,
)

_SYNTHETIC_ITEM_NAMESPACE = UUID("ca19dca4-103e-4a56-b388-e14b27f95d58")


def active_history(items: tuple[ConversationItem, ...]) -> tuple[ConversationItem, ...]:
    """Return one continuous window, preserving appended context update messages."""

    start = 0
    for index, item in enumerate(items):
        if isinstance(item, CompactionItem):
            start = index
    window = _reconstruct_compaction_replacement(items[start:])
    window = tuple(
        item for item in window if not (isinstance(item, CompactionItem) and item.context_reset)
    )
    return normalize_tool_pairs(render_context_history(window))


def _reconstruct_compaction_replacement(
    window: tuple[ConversationItem, ...],
) -> tuple[ConversationItem, ...]:
    """Move an append-only compaction marker to its model-visible position."""

    if not window or not isinstance(window[0], CompactionItem):
        return window
    marker = window[0]
    count = marker.replacement_item_count
    if count == 0:
        return window
    # A torn/corrupt replacement must not silently treat later conversation
    # items as replacement history. Persistence normally appends the complete
    # marker+replacement tuple in one transaction.
    if len(window) - 1 < count:
        raise ValueError(
            f"compaction replacement is incomplete: expected {count} items, found {len(window) - 1}"
        )
    replacement = window[1 : count + 1]
    rest = window[count + 1 :]
    index = marker.summary_insert_index
    return (*replacement[:index], marker, *replacement[index:], *rest)


def normalize_tool_pairs(
    items: tuple[ConversationItem, ...],
) -> tuple[ConversationItem, ...]:
    """Insert deterministic aborted results and remove orphan outputs."""

    calls = {str(item.call.id): item for item in items if isinstance(item, ToolCallItem)}
    results = {str(item.call_id) for item in items if isinstance(item, ToolResultItem)}
    normalized: list[ConversationItem] = []
    for item in items:
        if isinstance(item, ToolResultItem) and str(item.call_id) not in calls:
            continue
        normalized.append(item)
        if isinstance(item, ToolCallItem) and str(item.call.id) not in results:
            synthetic_id = ItemId(str(uuid5(_SYNTHETIC_ITEM_NAMESPACE, f"aborted:{item.call.id}")))
            normalized.append(
                ToolResultItem(
                    ToolCallId(str(item.call.id)),
                    item.call.name,
                    "aborted",
                    item.turn_id,
                    id=synthetic_id,
                    is_error=True,
                )
            )
    return tuple(normalized)
