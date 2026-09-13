"""Append-only context updates, separate from their durable comparison state.

AGENTS use explicit replacement/removal semantics; environments and deferred
namespaces use typed deltas. Remaining contributors publish full sections with
keyed replacement notices, not every Codex section's typed diff.
"""

import json
from dataclasses import replace
from uuid import UUID, uuid5

from corki.context import (
    deferred_tools,
    environment,
    model_instructions,
    model_transition,
    permissions,
    personality,
    plugin_guidance,
)
from corki.context.extension_world_state import (
    WorldStateSection,
    is_owned_section,
    render_section,
    retire_section,
)
from corki.context.input_context import is_input_context
from corki.prompting import PromptStore
from corki.protocol.ids import ItemId, TurnId, new_item_id
from corki.protocol.items import CompactionItem, ContextItem, ContextRole, ConversationItem

_LEGACY_REMOVAL_NAMESPACE = UUID("e4d1a1ef-a7d4-4888-88e7-e578be37edeb")
_PROMPTS = PromptStore()


def snapshot_content(item: ContextItem) -> str:
    """Return comparison data without the model-visible update notice."""
    return item.content if item.snapshot_content is None else item.snapshot_content


def _section_key(item: ContextItem) -> str:
    # Keep durable legacy keys, but compare mode variants as one world-state section.
    return "collaboration_mode" if item.key in {"mode.default", "mode.plan"} else item.key


def changed_context_items(
    history: tuple[ConversationItem, ...],
    snapshot: tuple[ContextItem, ...],
    turn_id: TurnId,
    *,
    omitted_sections: frozenset[str] = frozenset(),
    world_state_sections: tuple[WorldStateSection, ...] = (),
) -> tuple[ContextItem, ...]:
    """Append differences against only the currently retained window's baseline."""
    latest: dict[str, ContextItem] = {}
    previous_model: str | None = None
    for item in history:
        if isinstance(item, CompactionItem):
            latest.clear()
        elif isinstance(item, ContextItem) and not is_input_context(item):
            if (
                item.key == "legacy.developer"
                and item.role is ContextRole.DEVELOPER
                and item.content.strip().startswith("<personality_spec>")
                and item.content.strip().endswith("</personality_spec>")
            ):
                # The fragment proves section presence, not the old selection.
                # Keep it out of generic legacy revocation; a typed snapshot
                # establishes the comparison baseline without inventing a change.
                # A retained typed snapshot takes precedence over legacy prose,
                # even if the fragment occurs later in the journal.
                latest.setdefault(
                    personality.KEY,
                    replace(item, key=personality.KEY, snapshot_state="personality.legacy_unknown"),
                )
                continue
            if (
                item.key == "legacy.developer"
                and item.role is ContextRole.DEVELOPER
                and item.content.strip().startswith("<managed_developer_instructions>")
                and item.content.strip().endswith("</managed_developer_instructions>")
            ):
                # Reconcile an old untyped policy once without rewriting its row.
                latest["managed_developer_instructions"] = replace(
                    item,
                    key="managed_developer_instructions",
                    content_kind="managed_config.developer_instructions",
                    separate_message=True,
                    snapshot_state="managed_developer_instructions.legacy_unknown",
                )
                continue
            if item.key == model_transition.KEY and item.role is ContextRole.DEVELOPER:
                # Older histories persist accepted model identity without the
                # newer instruction section. Both survive window compaction.
                marker = model_transition.previous_model((item,))
                if marker is not None:
                    previous_model = marker["model"]
            if item.key == "model.instructions":
                # Compaction drops the section baseline, not the identity of
                # the last model used. Read append-only history for cold starts
                # too, without retaining old instructions in the visible window.
                restored = model_instructions.restored_model(item)
                if restored is not None:
                    previous_model = restored
            if (
                item.key == "legacy.developer"
                and item.role is ContextRole.DEVELOPER
                and item.content.strip().startswith("<context_window_guidance>")
                and item.content.strip().endswith("</context_window_guidance>")
            ):
                # Legacy Message rows have no section snapshot. Reconcile once,
                # without rewriting their durable key or treating user quotes
                # as host guidance. A later typed update supersedes this state.
                latest["context_window_guidance"] = replace(
                    item,
                    key="context_window_guidance",
                    snapshot_state="guidance.legacy_unknown",
                )
                continue
            if (
                item.key == "legacy.developer"
                and item.role is ContextRole.DEVELOPER
                and item.content.strip().startswith("<collaboration_mode>")
                and item.content.strip().endswith("</collaboration_mode>")
            ):
                # The old wire fragment does not identify a mode or model.
                # Mark only its section as unknown; the current host snapshot
                # supplies the replacement and establishes the known baseline.
                latest["collaboration_mode"] = replace(
                    item, snapshot_state="collaboration_mode.legacy_unknown"
                )
                continue
            latest[_section_key(item)] = item
    current_keys = {_section_key(item) for item in snapshot}
    extension_sections = {section.key: section for section in world_state_sections}
    changed: list[ContextItem] = []
    for item in snapshot:
        if is_input_context(item) or _section_key(item) in omitted_sections:
            continue
        previous = latest.get(_section_key(item))
        if item.key in extension_sections:
            update = render_section(
                extension_sections[item.key], replace(item, turn_id=turn_id), history
            )
            if update is not None:
                changed.append(update)
            continue
        content = snapshot_content(item)
        if (
            item.key == "model.instructions"
            and previous is not None
            and model_instructions.restored_model(previous)
            == json.loads(item.snapshot_state)["model"]
        ):
            continue
        if item.key == plugin_guidance.KEY and content:
            if plugin_guidance.is_retained(history):
                continue
            # A persisted comparison record cannot stand in for retained guidance.
            changed.append(replace(item, turn_id=turn_id))
            continue
        if previous is not None and (
            previous.role == item.role
            and previous.content_kind == item.content_kind
            and previous.separate_message == item.separate_message
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
        changed.append(_render_update(current, previous, previous_model=previous_model))
    changed.extend(
        _removal(item, turn_id)
        for key, item in latest.items()
        if key not in current_keys
        and key not in omitted_sections
        and snapshot_content(item)
        and not is_owned_section(item)
    )
    for key, item in latest.items():
        if is_owned_section(item) and key not in current_keys and key not in omitted_sections:
            retired = retire_section(replace(item, id=new_item_id(), turn_id=turn_id))
            if retired is not None:
                changed.append(retired)
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
        previous = latest.get(_section_key(item))
        latest[_section_key(item)] = item
        if item.is_snapshot_only:
            # Remember silent comparison records without submitting empty messages.
            continue
        if is_owned_section(item):
            rendered.append(item)
            continue
        if item.snapshot_content is not None:
            rendered.append(item)
            continue
        if item.key == "model.instructions" and not model_instructions.has_rendering_state(item):
            # Old visible text remains history even when its comparison or
            # rendering metadata cannot be recovered. Never rewrite the row.
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
        rendered.append(
            personality.render_history(item, previous)
            if item.key == personality.KEY
            else _render_update(item, previous)
        )
    return tuple(rendered)


def _removal(item: ContextItem, turn_id: TurnId) -> ContextItem:
    empty = ContextItem(
        item.key,
        item.role,
        "",
        turn_id,
        id=new_item_id(),
        content_kind=item.content_kind,
        separate_message=item.separate_message,
        snapshot_state="plugins.unavailable" if item.key == plugin_guidance.KEY else None,
    )
    if item.key == plugin_guidance.KEY:
        return empty
    return _render_update(empty, item)


def _render_update(
    item: ContextItem, previous: ContextItem | None, *, previous_model: str | None = None
) -> ContextItem:
    if item.key == personality.KEY:
        return personality.render_update(item, previous, previous_model)
    if item.key == "managed_developer_instructions":
        from corki.config.managed_instructions import REMOVAL, REPLACEMENT, render

        body = item.content
        if previous is not None and snapshot_content(previous):
            body = REPLACEMENT + "\n\n" + body if body else REMOVAL
        return replace(item, content=render(body) if body else "", snapshot_content=item.content)
    if item.key == "model.instructions":
        state = json.loads(item.snapshot_state)
        restored = model_instructions.restored_model(previous)
        previous_model = (
            restored
            if restored is not None
            else previous_model
            if previous_model is not None
            else state["base_model"]
        )
        content = ""
        if previous_model is not None and previous_model != state["model"] and item.content:
            content = _PROMPTS.render("context/model_switch", instructions=item.content).rstrip(
                "\n"
            )
        return replace(item, content=content, snapshot_content=item.content)
    if item.key in {"mode.default", "mode.plan"}:
        return replace(
            item,
            content=f"<collaboration_mode>{item.content}</collaboration_mode>",
            snapshot_content=item.content,
        )
    if item.key in (permissions.KEY, permissions.COMPACT_KEY):
        return permissions.render_update(item, previous)
    if item.key == environment.KEY:
        return environment.render_update(item, previous)
    if item.key == deferred_tools.KEY:
        return deferred_tools.render_update(item, previous)
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
    if item.key == "context_window_guidance":
        notice = _PROMPTS.render(
            "context/window_guidance_replacement"
            if item.content
            else "context/window_guidance_removal"
        ).strip()
        marker = "<context_window_guidance>\n"
        if item.content.startswith(marker):
            content = marker + notice + "\n\n" + item.content[len(marker) :]
        else:
            body = notice + ("\n\n" + item.content if item.content else "")
            content = marker + body + "\n</context_window_guidance>"
        return replace(item, content=content, snapshot_content=item.content)
    if item.key == plugin_guidance.CATALOG_KEY:
        notice = _PROMPTS.render("extensions/plugins/catalog_replacement").strip()
        content = notice + ("\n\n" + item.content if item.content else "")
        return replace(item, content=content, snapshot_content=item.content)
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
