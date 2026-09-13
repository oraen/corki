"""Small context-contribution boundary used by optional feature packages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from corki.prompting import PromptContribution
from corki.protocol.tools import ToolSpec


class StepContextContributor(Protocol):
    """Contribute from the exact immutable tool definitions used for this Step."""

    def step_contributions(
        self, *, tool_specs: tuple[ToolSpec, ...]
    ) -> tuple[PromptContribution, ...]: ...


@runtime_checkable
class ToolSnapshotContributor(Protocol):
    """Optional host provenance view; tool specs alone cannot identify dispatch owners."""

    def snapshot_contributions(self, *, tool_snapshot) -> tuple[PromptContribution, ...]: ...


class ContextContributor(Protocol):
    def contributions(
        self,
        *,
        cwd: Path,
        user_input: str,
        realtime_active: bool,
    ) -> tuple[PromptContribution, ...]: ...


@runtime_checkable
class InitialContextContributor(Protocol):
    """Developer fragments read only when a model-visible window is initialized.

    Declare all owned keys even when their current source is empty. This keeps
    steady-state world-state diffs from revoking historical initial context.
    """

    @property
    def initial_context_keys(self) -> tuple[str, ...]: ...


@runtime_checkable
class AsyncContextContributor(Protocol):
    """Optional owned async IO path, retaining synchronous embedding compatibility."""

    async def async_contributions(
        self, *, cwd: Path, user_input: str, realtime_active: bool
    ) -> tuple[PromptContribution, ...]: ...


@runtime_checkable
class ModelWindowContributor(Protocol):
    """Optional immutable view for a contributor whose allocation depends on the model."""

    def with_context_window(self, tokens: int) -> ContextContributor: ...


@dataclass(frozen=True, slots=True)
class InputContextContributions:
    """Selected input fragments and transient diagnostics, never model observations."""

    contributions: tuple[PromptContribution, ...] = ()
    warnings: tuple[str, ...] = ()


@runtime_checkable
class AsyncInputContextContributor(Protocol):
    """Async input reads preserve the caller's captured mentions and tool snapshot."""

    async def async_input_contributions(
        self, *, cwd: Path, user_input: str, mentions, tool_snapshot
    ) -> InputContextContributions | tuple[PromptContribution, ...]: ...


@runtime_checkable
class InputContextContributor(Protocol):
    """Optional turn-input selection, separate from ordinary world-state refresh."""

    def input_contributions(
        self, *, cwd: Path, user_input: str
    ) -> InputContextContributions | tuple[PromptContribution, ...]: ...


@runtime_checkable
class StructuredInputContextContributor(Protocol):
    """Optional typed selectors kept out of model-visible input text."""

    def input_contributions_with_mentions(
        self, *, cwd: Path, user_input: str, mentions
    ) -> InputContextContributions | tuple[PromptContribution, ...]: ...


@runtime_checkable
class SnapshotInputContextContributor(Protocol):
    """Resolve explicit input hints against the exact Step's executable provenance."""

    def input_snapshot_contributions(
        self, *, cwd: Path, user_input: str, mentions, tool_snapshot
    ) -> InputContextContributions | tuple[PromptContribution, ...]: ...
