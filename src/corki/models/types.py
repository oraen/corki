"""Normalized model requests and streaming events."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from corki.protocol.context import ModelContextInfo
from corki.protocol.items import ConversationItem
from corki.protocol.tools import ToolSpec
from corki.protocol.wire_numbers import WireNumber, validate_number


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
    harness_managed_retries: bool = False
    tool_freeform_mode: str = "compatible"
    # None inherits the adapter's setting; memory jobs provide per-request
    # effort without mutating a transport shared with the interactive turn.
    reasoning_effort: str | None = None
    tool_namespace_mode: str = "compatible"
    client_metadata: Mapping[str, str] | None = None
    # Legacy SDK fields retained for an explicit pre-network rejection. Runtime
    # summaries use ordinary requests and never set these obsolete controls.
    compaction_turn_id: str | None = None
    compaction_mode: str = "v2"
    # In-process transport ownership, deliberately absent from checkpoint/business state.
    # A resolved None must not inherit an unrelated model's adapter default.
    reasoning_effort_resolved: bool = False
    model_info: ModelContextInfo | None = None
    reasoning_summary: str | None = None
    service_tier: str | None = None
    fast_mode_enabled: bool = True
    # Host identity for request-only prefix IDs, never inferred from a user message.
    thread_id: str | None = None
    content_item_kinds: bool = True

    def __post_init__(self) -> None:
        if self.tool_search_mode == "native":
            object.__setattr__(self, "tool_search_mode", "compatible")
        if not isinstance(self.content_item_kinds, bool):
            raise ValueError("content_item_kinds must be a bool")
        if self.thread_id is not None and (
            not isinstance(self.thread_id, str) or not self.thread_id
        ):
            raise ValueError("thread_id must be a nonempty string or None")
        if self.service_tier is not None and not isinstance(self.service_tier, str):
            raise ValueError("service_tier must be a string or None")
        if not isinstance(self.fast_mode_enabled, bool):
            raise ValueError("fast_mode_enabled must be a bool")
        if self.model_info is not None and (
            not isinstance(self.model_info, ModelContextInfo) or self.model_info.model != self.model
        ):
            raise ValueError("model_info must describe the requested model")
        if self.reasoning_summary is not None and self.reasoning_summary not in (
            "auto",
            "concise",
            "detailed",
            "none",
        ):
            raise ValueError("invalid reasoning_summary")
        if not isinstance(self.reasoning_effort_resolved, bool):
            raise ValueError("reasoning_effort_resolved must be a bool")
        if self.compaction_mode not in ("v2", "legacy"):
            raise ValueError("compaction_mode must be v2 or legacy")
        if self.compaction_mode == "legacy" and self.compaction_turn_id is None:
            raise ValueError("legacy compaction requires a compaction turn")
        if self.compaction_turn_id is not None and (
            not isinstance(self.compaction_turn_id, str) or not self.compaction_turn_id
        ):
            raise ValueError("compaction_turn_id must be a nonempty string")
        if self.client_metadata is not None:
            if not isinstance(self.client_metadata, Mapping) or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in self.client_metadata.items()
            ):
                raise ValueError("client_metadata must map strings to strings")
            object.__setattr__(self, "client_metadata", dict(self.client_metadata))
        if self.tool_namespace_mode not in ("compatible", "native"):
            raise ValueError("tool_namespace_mode must be compatible or native")
        object.__setattr__(self, "tool_namespace_mode", "compatible")
        if self.reasoning_effort is not None and (
            not isinstance(self.reasoning_effort, str) or not self.reasoning_effort
        ):
            raise ValueError("reasoning_effort must be a non-empty string")
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
    total_tokens: int | None = None
    cache_write_tokens: int = 0
    codex_rollout_budget_units: int | float | WireNumber | None = None

    def __post_init__(self) -> None:
        for value in (
            self.input_tokens,
            self.output_tokens,
            self.cached_tokens,
            self.reasoning_tokens,
            self.total_tokens if self.total_tokens is not None else 0,
            self.cache_write_tokens,
        ):
            if type(value) is not int or not -(2**63) <= value < 2**63:
                raise ValueError("token usage must contain signed i64 integers")
        if self.codex_rollout_budget_units is not None:
            validate_number(self.codex_rollout_budget_units)

    @property
    def context_tokens(self) -> int | None:
        if self.total_tokens is not None:
            return self.total_tokens
        # Cached input and reasoning output are subsets, not additional costs.
        return (self.input_tokens + self.output_tokens) or None


@dataclass(frozen=True, slots=True)
class ModelTextDelta:
    delta: str
    item_id: str | None = None


@dataclass(frozen=True, slots=True)
class ModelReasoningDelta:
    """Provider reasoning kept separate from user-visible answer text."""

    delta: str
    item_id: str | None = None
    section_index: int | None = None
    channel: Literal["summary", "raw"] = "summary"


@dataclass(frozen=True, slots=True)
class ModelRetrying:
    attempt: int
    max_attempts: int
    delay_seconds: float
    error: str


@dataclass(frozen=True, slots=True)
class ModelItemCompleted:
    """An immutable, complete output item; not completion of the response."""

    item: ConversationItem


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


ModelEvent = (
    ModelTextDelta | ModelReasoningDelta | ModelRetrying | ModelItemCompleted | ModelCompleted
)
