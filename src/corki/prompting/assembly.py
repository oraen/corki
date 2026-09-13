"""Composable prompt layers used to build one model-visible context.

Feature packages contribute templates through this small contract.  The core
prompting package therefore does not need feature-specific branches when
permissions, realtime, plugins, or multi-agent support is added later.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from types import MappingProxyType

from corki.prompting.store import PromptStore


class PromptRole(StrEnum):
    """Model roles available to rendered prompt fragments."""

    DEVELOPER = "developer"
    USER = "user"


class PromptSlot(IntEnum):
    """Stable insertion points for current and planned prompt contributors.

    Values are intentionally spaced so a feature can use ``order`` for several
    fragments inside one slot without changing the cross-feature contract.
    Base API instructions are handled separately by :class:`PromptAssembler`.
    """

    SESSION = 100
    REALTIME = 200
    PROJECT_INSTRUCTIONS = 300
    HOST_SKILLS = 350
    PERMISSIONS = 400
    COLLABORATION_MODE = 500
    ENVIRONMENT = 600
    EXTENSIONS = 700
    WORLD_STATE_EXTENSIONS = 750
    MULTI_AGENT = 800
    TURN = 900


class PromptPhase(StrEnum):
    """Producer-owned initial context phase, independent of fragment ordering."""

    EXTENSION = "extension"
    WORLD_STATE = "world_state"


@dataclass(frozen=True, slots=True)
class PromptContribution:
    """A feature's request to add one template to the model context."""

    key: str
    template_name: str | None
    role: PromptRole
    slot: PromptSlot
    order: int = 0
    variables: Mapping[str, str] = field(default_factory=dict)
    separate_message: bool = False
    input_scoped: bool = False
    warnings: tuple[str, ...] = ()
    # Feature-owned comparison state, never prompt text. A None template emits
    # a silent snapshot rather than an empty model message.
    snapshot_state: str | None = None
    # Producer-owned provenance, independent of the stable key or rendered text.
    content_kind: str | None = None
    # None preserves legacy host slot semantics. Built-in producers set this explicitly.
    phase: PromptPhase | None = None

    def __post_init__(self) -> None:
        if self.phase is not None and not isinstance(self.phase, PromptPhase):
            raise TypeError("phase must be a PromptPhase or None")
        if self.content_kind is not None and not isinstance(self.content_kind, str):
            raise TypeError("content_kind must be a string or None")
        if not self.key or not self.key.strip():
            raise ValueError("prompt contribution key must not be empty")
        if self.template_name is None and self.snapshot_state is None:
            raise ValueError("a silent contribution requires snapshot state")
        if self.input_scoped and self.snapshot_state is not None:
            raise ValueError("input-attached history cannot carry world-state metadata")
        object.__setattr__(self, "variables", MappingProxyType(dict(self.variables)))


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    """One rendered contribution ready for the context builder."""

    key: str
    role: PromptRole
    content: str
    slot: PromptSlot
    order: int
    separate_message: bool
    input_scoped: bool = False
    warnings: tuple[str, ...] = ()
    snapshot_state: str | None = None
    content_kind: str | None = None

    def __post_init__(self) -> None:
        if self.content_kind is not None and not isinstance(self.content_kind, str):
            raise TypeError("content_kind must be a string or None")


@dataclass(frozen=True, slots=True)
class PromptAssembly:
    """Base API instructions plus ordered contextual prompt fragments."""

    instructions: str
    fragments: tuple[RenderedPrompt, ...]


class DuplicatePromptContributionError(ValueError):
    """Raised when two enabled features claim the same stable prompt key."""


class PromptAssembler:
    """Render independent feature contributions into a deterministic assembly."""

    def __init__(self, store: PromptStore) -> None:
        self._store = store

    def assemble(
        self,
        *,
        base_template: str,
        contributions: Iterable[PromptContribution],
        base_variables: Mapping[str, str] | None = None,
    ) -> PromptAssembly:
        """Render a base template and deterministically order enabled fragments."""

        indexed_contributions = list(enumerate(contributions))
        keys = [contribution.key for _, contribution in indexed_contributions]
        duplicate_keys = {key for key, count in Counter(keys).items() if count > 1}
        if duplicate_keys:
            names = ", ".join(sorted(duplicate_keys))
            raise DuplicatePromptContributionError(f"duplicate prompt contribution keys: {names}")

        indexed_contributions.sort(
            key=lambda item: (
                item[1].slot,
                item[1].order,
                item[0],
            )
        )
        fragments = tuple(
            RenderedPrompt(
                key=contribution.key,
                role=contribution.role,
                content=(
                    self._store.render(contribution.template_name, **contribution.variables)
                    if contribution.template_name is not None
                    else ""
                ),
                slot=contribution.slot,
                order=contribution.order,
                separate_message=contribution.separate_message,
                input_scoped=contribution.input_scoped,
                warnings=contribution.warnings,
                snapshot_state=contribution.snapshot_state,
                content_kind=contribution.content_kind,
            )
            for _, contribution in indexed_contributions
        )
        return PromptAssembly(
            instructions=self._store.render(base_template, **(base_variables or {})),
            fragments=fragments,
        )
