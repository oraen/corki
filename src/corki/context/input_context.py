"""Bind turn-input fragments to durable input identity, not world-state keys."""

from dataclasses import replace
from uuid import UUID, uuid5

from corki.protocol.ids import ItemId
from corki.protocol.items import ContextItem, ConversationItem, UserMessageItem

_INPUT_CONTEXT_NAMESPACE = UUID("6fc515bd-a3b0-445f-b1d7-cde7bf71185d")


def is_input_context(item: ContextItem) -> bool:
    """Recognize bound fragments and legacy selected-skill rows without migration."""
    return item.source_input_id is not None or item.key.startswith("extensions.skills.selected.")


def bind_input_context(
    history: tuple[ConversationItem, ...],
    active: tuple[ConversationItem, ...],
    fragments: tuple[ContextItem, ...],
    pending: tuple[ConversationItem, ...],
) -> tuple[ContextItem, ...]:
    """Freeze fragments once for the original turn input; replay only retained ones.

    Raw history proves prior injection even if compaction removed the full body.
    A repeated prepare must neither reread into a different durable message nor
    resurrect an already-compacted instruction body.
    """
    user = next((item for item in pending if isinstance(item, UserMessageItem)), None)
    if user is None:
        return ()
    original = next(
        (
            item
            for item in history
            if isinstance(item, UserMessageItem) and item.turn_id == user.turn_id
        ),
        user,
    )
    if original.id != user.id:
        return ()
    previous = {
        item.id
        for item in history
        if isinstance(item, ContextItem)
        and (
            item.source_input_id == user.id
            or (
                item.source_input_id is None
                and is_input_context(item)
                and item.turn_id == user.turn_id
            )
        )
    }
    if previous:
        return tuple(
            item for item in active if isinstance(item, ContextItem) and item.id in previous
        )
    return tuple(
        replace(
            item,
            id=ItemId(str(uuid5(_INPUT_CONTEXT_NAMESPACE, f"{user.id}:{item.key}"))),
            turn_id=user.turn_id,
            created_at=user.created_at,
            source_input_id=user.id,
            message_group_id=ItemId(str(uuid5(_INPUT_CONTEXT_NAMESPACE, f"{user.id}:{item.key}"))),
            message_group_index=0,
            message_group_size=1,
        )
        for item in fragments
    )
