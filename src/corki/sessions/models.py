"""Thread and turn lifecycle value objects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from corki.protocol.collaboration import ModeKind, validate_mode
from corki.protocol.ids import ItemId, ThreadId, TurnId
from corki.protocol.items import ConversationItem
from corki.protocol.settings import ModelSettingsSnapshot


@dataclass(frozen=True, slots=True)
class ContextUsage:
    """Latest committed sampling usage and its durable history boundary."""

    total_tokens: int
    anchor_id: ItemId
    server_reasoning_included: bool = False  # Legacy constructor field; no runtime effect.
    input_tokens: int | None = None
    sample_id: str | None = None


class TurnStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class DisplayTurn:
    id: TurnId
    status: TurnStatus
    error: str | None = None
    collaboration_mode: ModeKind = "default"

    def __post_init__(self) -> None:
        validate_mode(self.collaboration_mode, None)


@dataclass(frozen=True, slots=True)
class DisplayHistory:
    """One read snapshot; terminal metadata is never part of model history."""

    items: tuple[ConversationItem, ...]
    turns: tuple[DisplayTurn, ...]


@dataclass(frozen=True, slots=True)
class TurnRecord:
    id: TurnId
    thread_id: ThreadId
    status: TurnStatus
    user_input: str
    final_answer: str | None = None
    error: str | None = None
    operation: str = "normal"
    model_settings: ModelSettingsSnapshot | None = None
    base_instructions: str | None = None

    def __post_init__(self) -> None:
        if self.base_instructions is not None and not isinstance(self.base_instructions, str):
            raise ValueError("Turn base instructions must be a string or None")
        if self.operation not in {"normal", "compact"}:
            raise ValueError(f"unknown turn operation: {self.operation}")
        if self.model_settings is not None and not isinstance(
            self.model_settings, ModelSettingsSnapshot
        ):
            raise ValueError("Turn model settings must be a validated snapshot")


@dataclass(frozen=True, slots=True)
class ThreadRecord:
    id: ThreadId
    cwd: Path
    created_at: str
    updated_at: str
    archived_at: str | None = None
    has_history: bool = False


class ToolExecutionStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
