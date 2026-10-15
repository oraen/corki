"""Read recorded context-message boundaries without regrouping mutable history."""

from collections.abc import Iterable, Iterator

from corki.protocol.items import ContextItem, ConversationItem


def context_message_groups(
    items: Iterable[ConversationItem],
) -> Iterator[ConversationItem | tuple[ContextItem, ...]]:
    """Yield complete recorded messages; old ungrouped fragments remain singletons.

    Silent comparison records are not model messages. Group metadata is host-owned;
    a torn or mismatched group is a history error, not permission to change its body.
    """
    visible = iter(
        item for item in items if not (isinstance(item, ContextItem) and item.is_snapshot_only)
    )
    seen: set[str] = set()
    for item in visible:
        if not isinstance(item, ContextItem):
            yield item
            continue
        if item.message_group_id is None:
            yield (item,)
            continue
        group_id = item.message_group_id
        if item.message_group_index != 0 or group_id != item.id or group_id in seen:
            raise ValueError("context message group is not contiguous or starts mid-message")
        seen.add(group_id)
        group = [item]
        for index in range(1, item.message_group_size):
            part = next(visible, None)
            if (
                not isinstance(part, ContextItem)
                or part.message_group_id != group_id
                or part.message_group_index != index
                or part.message_group_size != item.message_group_size
                or part.role != item.role
                or part.turn_id != item.turn_id
            ):
                raise ValueError("context message group is incomplete or inconsistent")
            group.append(part)
        yield tuple(group)
