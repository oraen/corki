"""Thread and turn lifecycle value objects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from corki.protocol.ids import ItemId, ThreadId, TurnId


@dataclass(frozen=True, slots=True)
class ContextUsage:
    """Latest committed sampling usage and its durable history boundary."""

    total_tokens: int
    anchor_id: ItemId
    server_reasoning_included: bool = False
    input_tokens: int | None = None
    sample_id: str | None = None


class TurnStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class TurnRecord:
    id: TurnId
    thread_id: ThreadId
    status: TurnStatus
    user_input: str
    final_answer: str | None = None
    error: str | None = None
    operation: str = "normal"

    def __post_init__(self) -> None:
        if self.operation not in {"normal", "compact"}:
            raise ValueError(f"unknown turn operation: {self.operation}")


@dataclass(frozen=True, slots=True)
class ThreadRecord:
    id: ThreadId
    cwd: Path
    created_at: str
    updated_at: str


class ToolExecutionStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
