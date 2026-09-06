"""Typed runtime events observed by CLI and future external adapters."""

from __future__ import annotations

from dataclasses import dataclass

from corki.protocol.ids import ThreadId, ToolCallId, TurnId


@dataclass(frozen=True, slots=True)
class TurnStarted:
    thread_id: ThreadId
    turn_id: TurnId
    resumed: bool = False


@dataclass(frozen=True, slots=True)
class AssistantTextDelta:
    thread_id: ThreadId
    turn_id: TurnId
    delta: str


@dataclass(frozen=True, slots=True)
class AssistantReasoningDelta:
    """A streamed reasoning fragment, separate from the final answer channel."""

    thread_id: ThreadId
    turn_id: TurnId
    delta: str


@dataclass(frozen=True, slots=True)
class ModelRetryScheduled:
    thread_id: ThreadId
    turn_id: TurnId
    attempt: int
    max_attempts: int
    delay_seconds: float
    error: str


@dataclass(frozen=True, slots=True)
class TokenUsageUpdated:
    thread_id: ThreadId
    turn_id: TurnId
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    reasoning_tokens: int


@dataclass(frozen=True, slots=True)
class ContextCompacted:
    thread_id: ThreadId
    turn_id: TurnId
    estimated_tokens: int


@dataclass(frozen=True, slots=True)
class AssistantMessageCompleted:
    thread_id: ThreadId
    turn_id: TurnId
    text: str


@dataclass(frozen=True, slots=True)
class AssistantMessageInterrupted:
    thread_id: ThreadId
    turn_id: TurnId


@dataclass(frozen=True, slots=True)
class RealtimeInputAccepted:
    thread_id: ThreadId
    turn_id: TurnId
    text: str


@dataclass(frozen=True, slots=True)
class ToolCallStarted:
    thread_id: ThreadId
    turn_id: TurnId
    tool_call_id: ToolCallId
    tool_name: str
    arguments_preview: str


@dataclass(frozen=True, slots=True)
class ToolOutputDelta:
    thread_id: ThreadId
    turn_id: TurnId
    tool_call_id: ToolCallId
    delta: str


@dataclass(frozen=True, slots=True)
class ToolCallCompleted:
    thread_id: ThreadId
    turn_id: TurnId
    tool_call_id: ToolCallId
    tool_name: str
    is_error: bool


@dataclass(frozen=True, slots=True)
class PlanUpdated:
    thread_id: ThreadId
    turn_id: TurnId
    plan: tuple[dict[str, str], ...]


@dataclass(frozen=True, slots=True)
class TurnCompleted:
    thread_id: ThreadId
    turn_id: TurnId
    final_answer: str


@dataclass(frozen=True, slots=True)
class TurnFailed:
    thread_id: ThreadId
    turn_id: TurnId
    error: str
    error_kind: str | None = None
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class TurnCancelled:
    thread_id: ThreadId
    turn_id: TurnId


RuntimeEvent = (
    TurnStarted
    | AssistantTextDelta
    | AssistantReasoningDelta
    | ModelRetryScheduled
    | TokenUsageUpdated
    | ContextCompacted
    | AssistantMessageCompleted
    | AssistantMessageInterrupted
    | RealtimeInputAccepted
    | ToolCallStarted
    | ToolOutputDelta
    | ToolCallCompleted
    | PlanUpdated
    | TurnCompleted
    | TurnFailed
    | TurnCancelled
)
