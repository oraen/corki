"""Persistence contract for extraction jobs and consolidated memories."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from corki.memory.models import ConsolidationClaim, MemoryExtractionClaim, StageOneMemory
from corki.protocol.ids import ThreadId


class MemoryRepository(Protocol):
    async def claim_extraction_jobs(
        self,
        *,
        current_thread_id: ThreadId,
        max_age_days: int,
        min_idle_hours: int,
        limit: int,
        lease_seconds: int,
    ) -> tuple[MemoryExtractionClaim, ...]: ...

    async def complete_extraction(
        self,
        claim: MemoryExtractionClaim,
        memory: StageOneMemory | None,
    ) -> bool: ...

    async def fail_extraction(
        self,
        claim: MemoryExtractionClaim,
        error: str,
        *,
        retry_delay_seconds: int,
    ) -> bool: ...

    async def claim_consolidation(self, *, lease_seconds: int) -> ConsolidationClaim | None: ...

    async def enqueue_consolidation(self, *, force: bool = False) -> bool: ...

    async def load_consolidation_inputs(
        self, *, limit: int, max_unused_days: int
    ) -> tuple[StageOneMemory, ...]: ...

    async def complete_consolidation(
        self,
        claim: ConsolidationClaim,
        selected: tuple[StageOneMemory, ...],
    ) -> bool: ...

    async def fail_consolidation(
        self,
        claim: ConsolidationClaim,
        error: str,
        *,
        retry_delay_seconds: int,
    ) -> bool: ...

    async def mark_thread_mode(self, thread_id: ThreadId, mode: str) -> None: ...

    async def mark_memories_used(self, thread_ids: Iterable[ThreadId]) -> None: ...

    async def close(self) -> None: ...
