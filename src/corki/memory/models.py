"""Domain records for the Codex-style long-term memory pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from corki.protocol.ids import ThreadId
from corki.protocol.items import ConversationItem


class ThreadMemoryMode(StrEnum):
    """Whether a thread may contribute facts to long-term memory."""

    ENABLED = "enabled"
    DISABLED = "disabled"
    POLLUTED = "polluted"


@dataclass(frozen=True, slots=True)
class MemoryExtractionClaim:
    """One leased historical thread ready for phase-one extraction."""

    thread_id: ThreadId
    cwd: Path
    source_updated_at: str
    ownership_token: str
    items: tuple[ConversationItem, ...]


@dataclass(frozen=True, slots=True)
class StageOneMemory:
    """Structured model output extracted from one historical thread."""

    thread_id: ThreadId
    cwd: Path
    source_updated_at: str
    raw_memory: str
    rollout_summary: str
    rollout_slug: str | None = None
    usage_count: int = 0
    last_used_at: str | None = None


@dataclass(frozen=True, slots=True)
class ConsolidationClaim:
    """Singleton lease protecting the shared memory workspace."""

    ownership_token: str
    input_watermark: int


@dataclass(frozen=True, slots=True)
class MemorySkill:
    """A reusable procedure distilled during global consolidation."""

    name: str
    description: str
    content: str


@dataclass(frozen=True, slots=True)
class ConsolidatedMemory:
    """Validated phase-two files returned by the consolidation model."""

    memory: str
    summary: str
    skills: tuple[MemorySkill, ...] = ()
