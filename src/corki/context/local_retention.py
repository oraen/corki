"""Local compaction user evidence: native text policy plus optional request headroom."""

from dataclasses import replace
from typing import TypeVar
from uuid import uuid4

from corki.context.hosted_output import truncate_output_text
from corki.context.tokens import estimate_item_tokens
from corki.protocol.ids import ItemId
from corki.protocol.items import ContextItem, ConversationItem, UserMessageItem
from corki.protocol.tools import TextContent
from corki.protocol.truncation import TruncationPolicy

_ItemT = TypeVar("_ItemT", bound=ConversationItem)


def retained_user_messages(
    history: tuple[ConversationItem, ...],
    *,
    excluded_ids: set[ItemId],
    token_budget: int,
    request_token_budget: int | None = None,
) -> tuple[UserMessageItem, ...]:
    """Retain newest user text under compact.rs's UTF-8 bytes/4 policy.

    The native text budget excludes message overhead and the truncation marker.
    Automatic compaction may also impose a conservative whole-request allowance;
    that independent limit includes Unicode, annotations and the marker itself.
    """
    selected = []
    remaining = token_budget
    for item in reversed(history):
        if remaining <= 0:
            break
        if not isinstance(item, UserMessageItem) or item.id in excluded_ids:
            continue
        text = (
            item.content
            if item.image_positions
            else (
                "\n".join(
                    p.text for p in item.content_items if isinstance(p, TextContent) and p.text
                )
                if item.content_items
                else item.content
            )
        )
        item = replace(
            item,
            content=text,
            content_items=(),
            attachments=(),
            image_positions=(),
            content_item_kinds=("user.text",) if item.content_item_kinds is not None else None,
        )
        cost = (len(text.encode("utf-8")) + 3) // 4
        policy = TruncationPolicy("tokens", remaining)
        candidate = replace(item, content=truncate_output_text(text, policy))
        if request_token_budget is not None:
            if estimate_item_tokens(candidate) > request_token_budget:
                # Find a fitting prefix+suffix, always measuring the complete
                # retained item. Never shorten the separately protected input.
                low, high = 0, min(remaining, cost)
                candidate = replace(
                    item, content=truncate_output_text(text, replace(policy, limit=0))
                )
                if estimate_item_tokens(candidate) > request_token_budget:
                    break
                while low < high:
                    middle = (low + high + 1) // 2
                    proposed = replace(
                        item, content=truncate_output_text(text, replace(policy, limit=middle))
                    )
                    if estimate_item_tokens(proposed) <= request_token_budget:
                        low, candidate = middle, proposed
                    else:
                        high = middle - 1
            request_token_budget -= estimate_item_tokens(candidate)
        selected.append(retained_copy(candidate))
        if cost > remaining or candidate.content != text:
            break
        remaining -= cost
    selected.reverse()
    return tuple(selected)


def retained_copy(item: _ItemT) -> _ItemT:
    """Copy a persisted item without inventing a fresh user contribution."""
    if isinstance(item, UserMessageItem):
        return replace(
            item, id=ItemId(str(uuid4())), retained_from_id=item.retained_from_id or item.id
        )
    if isinstance(item, ContextItem) and item.message_group_id is not None:
        if item.message_group_size != 1:
            raise ValueError("cannot retain one fragment of a grouped context message")
        new_id = ItemId(str(uuid4()))
        return replace(item, id=new_id, message_group_id=new_id)
    return replace(item, id=ItemId(str(uuid4())))
