"""Small context-contribution boundary used by optional feature packages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from corki.prompting import PromptContribution


class ContextContributor(Protocol):
    def contributions(
        self,
        *,
        cwd: Path,
        user_input: str,
        realtime_active: bool,
    ) -> tuple[PromptContribution, ...]: ...


@dataclass(frozen=True, slots=True)
class InputContextContributions:
    """Selected input fragments and transient diagnostics, never model observations."""

    contributions: tuple[PromptContribution, ...] = ()
    warnings: tuple[str, ...] = ()


@runtime_checkable
class InputContextContributor(Protocol):
    """Optional turn-input selection, separate from ordinary world-state refresh."""

    def input_contributions(
        self, *, cwd: Path, user_input: str
    ) -> InputContextContributions | tuple[PromptContribution, ...]: ...
