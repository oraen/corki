"""Freeze initial bundles and incremental adjacency before atomic history writes."""

from dataclasses import replace

from corki.context.input_context import is_input_context
from corki.protocol.items import CompactionItem, ContextItem, ContextRole


def needs_initial_context(history) -> bool:
    has_baseline = False
    for item in history:
        if isinstance(item, CompactionItem):
            has_baseline = False
        elif isinstance(item, ContextItem) and not is_input_context(item):
            has_baseline = True
    return not has_baseline


def freeze_context_messages(
    items: tuple[ContextItem, ...],
    *,
    initial: bool,
    initial_extension_keys: frozenset[str] = frozenset(),
) -> tuple[ContextItem, ...]:
    """Assign fresh membership from the final rendered fragments, never old history."""
    silent = tuple(item for item in items if item.is_snapshot_only)
    visible = tuple(item for item in items if not item.is_snapshot_only)
    groups: list[list[ContextItem]] = []
    if initial:
        developer = [
            i for i in visible if i.role is ContextRole.DEVELOPER and not i.separate_message
        ]
        # Host thread/turn extensions precede world-state developer fragments.
        # Stable partition only for a new window; incremental adjacency is unchanged.
        developer.sort(key=lambda item: item.key not in initial_extension_keys)
        standalone = [i for i in visible if i.role is ContextRole.DEVELOPER and i.separate_message]
        users = [i for i in visible if i.role is ContextRole.USER]
        groups = [developer, *([i] for i in standalone), users]
    else:
        for item in visible:
            if (
                groups
                and groups[-1][-1].role == item.role
                and not groups[-1][-1].separate_message
                and not item.separate_message
            ):
                groups[-1].append(item)
            else:
                groups.append([item])
    return (
        *(
            replace(
                item,
                message_group_id=group[0].id,
                message_group_index=index,
                message_group_size=len(group),
            )
            for group in groups
            for index, item in enumerate(group)
        ),
        *(
            replace(i, message_group_id=None, message_group_index=0, message_group_size=1)
            for i in silent
        ),
    )
