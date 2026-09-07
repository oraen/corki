"""Append-only context updates, separate from their durable comparison state.

AGENTS updates use Codex's explicit replacement/removal semantics. Other Corki
contributors currently publish full sections, so they use keyed replacement
notices rather than pretending to implement every Codex section's typed diff.
"""

from dataclasses import replace
from uuid import UUID, uuid5

from corki.context.input_context import is_input_context
from corki.prompting import PromptStore
from corki.protocol.ids import ItemId, TurnId, new_item_id
from corki.protocol.items import CompactionItem, ContextItem, ConversationItem

_LEGACY_REMOVAL_NAMESPACE = UUID("e4d1a1ef-a7d4-4888-88e7-e578be37edeb")
_PROMPTS = PromptStore()


def snapshot_content(item: ContextItem) -> str:
    """Return comparison data without the model-visible update notice."""
    return item.content if item.snapshot_content is None else item.snapshot_content


def changed_context_items(
    history: tuple[ConversationItem, ...],
    snapshot: tuple[ContextItem, ...],
    turn_id: TurnId,
) -> tuple[ContextItem, ...]:
    """Append differences against only the currently retained window's baseline."""
    latest: dict[str, ContextItem] = {}
    for item in history:
        if isinstance(item, CompactionItem):
            latest.clear()
        elif isinstance(item, ContextItem) and not is_input_context(item):
            latest[item.key] = item
    current_keys = {item.key for item in snapshot}
    changed: list[ContextItem] = []
    for item in snapshot:
        if is_input_context(item):
            continue
        previous = latest.get(item.key)
        content = snapshot_content(item)
        if previous is not None and (
            previous.role == item.role
            and snapshot_content(previous) == content
            and previous.snapshot_state == item.snapshot_state
        ):
            continue
        if (
            not content
            and item.snapshot_state is None
            and (previous is None or not snapshot_content(previous))
        ):
            continue
        if previous is not None and previous.role != item.role and snapshot_content(previous):
            # A lower-role update cannot revoke an earlier higher-role message.
            # The host first retires its own old-role section, then introduces
            # the new-role section without promoting the latter's contents.
            changed.append(_removal(previous, turn_id))
            previous = None
            if not content:
                continue
        current = replace(item, turn_id=turn_id, content=content, snapshot_content=None)
        changed.append(_render_update(current, previous))
    changed.extend(
        _removal(item, turn_id)
        for key, item in latest.items()
        if key not in current_keys and snapshot_content(item)
    )
    return tuple(changed)


def render_context_history(
    items: tuple[ConversationItem, ...],
) -> tuple[ConversationItem, ...]:
    """Preserve messages; render legacy snapshots without rewriting stored rows."""
    latest: dict[str, ContextItem] = {}
    rendered: list[ConversationItem] = []
    for item in items:
        if not isinstance(item, ContextItem):
            rendered.append(item)
            continue
        if is_input_context(item):
            # Old releases treated selected bodies as replaceable snapshots.
            # Drop their artificial tombstones and unwrap replacement notices
            # only in the request view, leaving all stored payloads untouched.
            content = snapshot_content(item)
            if content:
                rendered.append(replace(item, content=content, snapshot_content=None))
            continue
        previous = latest.get(item.key)
        latest[item.key] = item
        if item.is_snapshot_only:
            # Remember silent comparison records without submitting empty messages.
            continue
        if item.snapshot_content is not None:
            rendered.append(item)
            continue
        if not item.content and (previous is None or not snapshot_content(previous)):
            continue
        if previous is not None and previous.role != item.role and snapshot_content(previous):
            # A deterministic projection for old rows, never a new durable fact.
            removal = _removal(previous, item.turn_id)
            rendered.append(
                replace(
                    removal,
                    id=ItemId(str(uuid5(_LEGACY_REMOVAL_NAMESPACE, str(item.id)))),
                    created_at=item.created_at,
                )
            )
            previous = None
            if not item.content:
                continue
        rendered.append(_render_update(item, previous))
    return tuple(rendered)


def _removal(item: ContextItem, turn_id: TurnId) -> ContextItem:
    empty = ContextItem(item.key, item.role, "", turn_id, id=new_item_id())
    return _render_update(empty, item)


def _render_update(item: ContextItem, previous: ContextItem | None) -> ContextItem:
    if item.key == "extensions.skills.catalog" and item.snapshot_state is not None:
        content = item.content
        if not content and previous is not None:
            content = _PROMPTS.render(
                "extensions/skills/catalog_hidden"
                if item.snapshot_state == "skills.hidden"
                else "extensions/skills/catalog_removed"
            )
        return replace(item, content=content, snapshot_content=item.content)
    if previous is None or not snapshot_content(previous):
        return item
    if item.key == "extensions.skills.catalog":
        # Codex emits a fresh catalog fragment, not a generic instruction revocation.
        content = item.content or _PROMPTS.render("extensions/skills/catalog_removed")
        return replace(item, content=content, snapshot_content=item.content)
    if item.key == "project.agents":
        notice = _PROMPTS.render(
            "context/agents_replacement_notice" if item.content else "context/agents_removal_notice"
        ).strip()
        marker = "<INSTRUCTIONS>\n"
        if item.content.startswith("# AGENTS.md instructions") and marker in item.content:
            content = item.content.replace(marker, marker + notice + "\n\n", 1)
        else:
            body = notice + ("\n\n" + item.content if item.content else "")
            content = f"# AGENTS.md instructions\n\n<INSTRUCTIONS>\n{body}\n</INSTRUCTIONS>"
    else:
        notice = _PROMPTS.render("context/section_removal_notice", key=repr(item.key)).strip()
        content = notice + ("\n\nCurrent context:\n" + item.content if item.content else "")
    return replace(item, content=content, snapshot_content=item.content)
