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
        # Initial Codex context has named placement rules; the same fragments
        # remain standalone during incremental updates. Match host-owned keys,
        # never markers quoted in user/tool content, and leave old groups intact.
        bundled = {"model.instructions", "personality"}
        tail = "managed_developer_instructions"
        developer = [
            i
            for i in visible
            if i.role is ContextRole.DEVELOPER
            and i.key != tail
            and (not i.separate_message or i.key in bundled)
        ]
        # A new model's instructions lead even the thread/turn extensions.
        developer.sort(
            key=lambda item: (
                0
                if item.key == "model.instructions"
                else 1
                if item.key in initial_extension_keys
                else 2
            )
        )
        standalone = [
            i
            for i in visible
            if i.role is ContextRole.DEVELOPER
            and i.separate_message
            and i.key not in bundled
            and i.key != tail
        ]
        users = [i for i in visible if i.role is ContextRole.USER]
        managed = [i for i in visible if i.role is ContextRole.DEVELOPER and i.key == tail]
        groups = [developer, *([i] for i in standalone), users, *([i] for i in managed)]
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
