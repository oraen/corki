"""Normalized model requests and streaming events."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from corki.protocol.items import ConversationItem
from corki.protocol.tools import ToolSpec


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """Complete provider-neutral snapshot used for one model step."""

    model: str
    instructions: str
    context_items: tuple[ConversationItem, ...]
    items: tuple[ConversationItem, ...]
    tools: tuple[ToolSpec, ...]
    output_schema: Mapping[str, Any] | None = None
    output_schema_name: str = "structured_output"
    tool_search_mode: str = "compatible"

    def __post_init__(self) -> None:
        if self.output_schema is not None:
            object.__setattr__(self, "output_schema", dict(self.output_schema))
        if not self.output_schema_name.strip():
            raise ValueError("output_schema_name must not be empty")


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ModelTextDelta:
    delta: str


@dataclass(frozen=True, slots=True)
class ModelReasoningDelta:
    """Provider reasoning kept separate from user-visible answer text."""

    delta: str


@dataclass(frozen=True, slots=True)
class ModelRetrying:
    attempt: int
    max_attempts: int
    delay_seconds: float
    error: str


@dataclass(frozen=True, slots=True)
class ModelCompleted:
    items: tuple[ConversationItem, ...]
    usage: ModelUsage = field(default_factory=ModelUsage)
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)
    # Completion of a sampling request is distinct from completion of a turn.
    # None means the provider did not specify a continuation signal.
    end_turn: bool | None = None

    def __post_init__(self) -> None:
        if self.end_turn is not None and not isinstance(self.end_turn, bool):
            raise ValueError("end_turn must be a boolean or None")


ModelEvent = ModelTextDelta | ModelReasoningDelta | ModelRetrying | ModelCompleted
