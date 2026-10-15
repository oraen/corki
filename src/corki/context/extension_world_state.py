"""Producer-owned step diffs; only comparison data crosses the persistence boundary."""

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from corki.protocol.items import CompactionItem, ContextItem, ContextRole, ConversationItem

PREFIX = "extension.world_state."


def is_owned_section(item: ContextItem) -> bool:
    """Require explicit producer provenance, not a previously valid host key prefix."""
    return item.key.startswith(PREFIX) and item.content_kind == item.key + ".instructions"


def _comparison_json(raw: str) -> str:
    def without_object_nulls(value):
        # Match the reference snapshot contract: arrays are atomic values, so
        # nulls (including object members inside arrays) remain untouched.
        if isinstance(value, dict):
            return {
                key: without_object_nulls(child)
                for key, child in value.items()
                if child is not None
            }
        return value

    value = json.loads(raw)
    if value is None:
        raise ValueError("extension world-state snapshot must not be null")
    return json.dumps(without_object_nulls(value), sort_keys=True, allow_nan=False)


class PreviousKind(StrEnum):
    ABSENT = "absent"
    UNKNOWN = "unknown"
    KNOWN = "known"


@dataclass(frozen=True, slots=True)
class PreviousSection:
    kind: PreviousKind
    snapshot_json: str | None = None


@dataclass(frozen=True, slots=True)
class WorldStateFragment:
    role: ContextRole
    content: str

    def __post_init__(self):
        if not isinstance(self.role, ContextRole) or not isinstance(self.content, str):
            raise ValueError("invalid extension world-state fragment")


@dataclass(frozen=True, slots=True)
class WorldStateSection:
    """One captured step: immutable JSON data plus host-owned rendering callbacks.

    Use a stable extension-local ID. The harness namespaces its durable key so
    it cannot collide with permissions, model instructions or input attachments.
    Renderers must be pure: preparation can evaluate a candidate more than once.
    """

    id: str
    snapshot_json: str
    render_diff: Callable[[PreviousSection], WorldStateFragment | None]
    legacy_matcher: Callable[[ContextRole, str], bool] | None = None
    retained_matcher: Callable[[ContextRole, str], bool] | None = None

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("extension world-state ID must not be empty")
        value = json.loads(self.snapshot_json)
        if value is None:
            raise ValueError("extension world-state snapshot must not be null")
        object.__setattr__(
            self, "snapshot_json", json.dumps(value, sort_keys=True, allow_nan=False)
        )
        for callback in (self.render_diff, self.legacy_matcher, self.retained_matcher):
            if callback is not None and not callable(callback):
                raise TypeError("extension world-state callback must be callable")
        if self.render_diff is None:
            raise TypeError("extension world-state renderer is required")

    @property
    def key(self) -> str:
        return PREFIX + self.id

    @property
    def state(self) -> str:
        return json.dumps(
            {"version": 1, "active": True, "snapshot": _comparison_json(self.snapshot_json)}
        )


@runtime_checkable
class WorldStateContributor(Protocol):
    """Capture extension sections independently of legacy prompt contributions."""

    async def world_state_contributions(
        self, *, cwd: Path, user_input: str, realtime_active: bool
    ) -> tuple[WorldStateSection, ...]: ...


def render_section(
    section: WorldStateSection,
    item: ContextItem,
    history: tuple[ConversationItem, ...],
) -> ContextItem | None:
    """Resolve retained-window provenance without rewriting any historical row."""
    retained: list[ContextItem] = []
    for row in history:
        if isinstance(row, CompactionItem):
            retained.clear()
        elif isinstance(row, ContextItem) and row.source_input_id is None:
            retained.append(row)
    previous = next((row for row in reversed(retained) if row.key == section.key), None)
    state = PreviousSection(PreviousKind.ABSENT)
    if previous is not None:
        try:
            if not is_owned_section(previous):
                raise ValueError("legacy host state has no extension provenance")
            saved = json.loads(previous.snapshot_state)
            if (
                type(saved["version"]) is not int
                or saved["version"] != 1
                or type(saved["active"]) is not bool
            ):
                raise ValueError("invalid extension snapshot envelope")
            if saved["active"]:
                canonical = _comparison_json(saved["snapshot"])
                state = PreviousSection(PreviousKind.KNOWN, canonical)
        except (TypeError, ValueError, KeyError, RecursionError):
            state = PreviousSection(PreviousKind.UNKNOWN)
    elif section.legacy_matcher is not None and any(
        section.legacy_matcher(row.role, row.content) for row in retained if row.content
    ):
        state = PreviousSection(PreviousKind.UNKNOWN)
    if (
        state.kind is PreviousKind.KNOWN
        and section.retained_matcher is not None
        and not any(
            section.retained_matcher(row.role, row.content) for row in retained if row.content
        )
    ):
        state = PreviousSection(PreviousKind.ABSENT)
    fragment = section.render_diff(state)
    if fragment is not None and not isinstance(fragment, WorldStateFragment):
        raise TypeError("extension renderer must return WorldStateFragment or None")
    if (
        fragment is None
        and previous is not None
        and is_owned_section(previous)
        and previous.snapshot_state == section.state
    ):
        return None
    return replace(
        item,
        role=fragment.role if fragment is not None else ContextRole.DEVELOPER,
        content=fragment.content if fragment is not None else "",
        snapshot_content="",
        snapshot_state=section.state,
        content_kind=section.key + ".instructions",
    )


def retire_section(item: ContextItem) -> ContextItem | None:
    """Record disappearance silently so reattachment sees Absent, including cold starts."""
    state = json.dumps({"version": 1, "active": False})
    if item.snapshot_state == state:
        return None
    return replace(item, content="", snapshot_content="", snapshot_state=state)
