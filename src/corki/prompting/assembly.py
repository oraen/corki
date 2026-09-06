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
    PERMISSIONS = 400
    COLLABORATION_MODE = 500
    ENVIRONMENT = 600
    EXTENSIONS = 700
    MULTI_AGENT = 800
    TURN = 900


@dataclass(frozen=True, slots=True)
class PromptContribution:
    """A feature's request to add one template to the model context."""

    key: str
    template_name: str
    role: PromptRole
    slot: PromptSlot
    order: int = 0
    variables: Mapping[str, str] = field(default_factory=dict)
    separate_message: bool = False

    def __post_init__(self) -> None:
        if not self.key or not self.key.strip():
            raise ValueError("prompt contribution key must not be empty")
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

        role_order = {PromptRole.DEVELOPER: 0, PromptRole.USER: 1}
        indexed_contributions.sort(
            key=lambda item: (
                role_order[item[1].role],
                item[1].slot,
                item[1].order,
                item[0],
            )
        )
        fragments = tuple(
            RenderedPrompt(
                key=contribution.key,
                role=contribution.role,
                content=self._store.render(contribution.template_name, **contribution.variables),
                slot=contribution.slot,
                order=contribution.order,
                separate_message=contribution.separate_message,
            )
            for _, contribution in indexed_contributions
        )
        return PromptAssembly(
            instructions=self._store.render(base_template, **(base_variables or {})),
            fragments=fragments,
        )
