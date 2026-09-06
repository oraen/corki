"""Thread and turn lifecycle value objects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from corki.protocol.ids import ThreadId, TurnId


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
