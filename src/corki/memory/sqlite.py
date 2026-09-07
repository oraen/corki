"""Transactional SQLite job store for long-term memory generation.

The main session repository owns conversation writes.  This adapter shares the
same database file but owns only memory-specific tables and the thread memory
mode column, mirroring Codex's separation between thread state and memories.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypeVar

from corki.memory.consolidation import claim_consolidation, enqueue_consolidation
from corki.memory.extraction import claim_extraction_jobs
from corki.memory.models import ConsolidationClaim, MemoryExtractionClaim, StageOneMemory
from corki.protocol.ids import ThreadId

_STAGE_ONE = "memory_stage1"
_CONSOLIDATE = "memory_consolidate_global"
_GLOBAL_KEY = "global"
_WriteResult = TypeVar("_WriteResult")


class SQLiteMemoryRepository:
    """Claim memory work with leases so concurrent Corki processes do not duplicate it."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._create_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _create_schema(self) -> None:
        with self._connect() as connection:
            # Serialize schema extension across simultaneously starting CLI
            # processes; otherwise two readers can both observe the missing
            # column before either ALTER commits.
            connection.execute("BEGIN IMMEDIATE")
            thread_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(threads)").fetchall()
            }
            if "memory_mode" not in thread_columns:
                connection.execute(
                    "ALTER TABLE threads ADD COLUMN memory_mode TEXT NOT NULL DEFAULT 'enabled'"
                )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_stage1_outputs (
                    thread_id TEXT PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE,
                    source_updated_at TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    raw_memory TEXT NOT NULL,
                    rollout_summary TEXT NOT NULL,
                    rollout_slug TEXT,
                    generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    usage_count INTEGER NOT NULL DEFAULT 0,
                    last_used_at TEXT,
                    selected_for_phase2 INTEGER NOT NULL DEFAULT 0,
                    selected_source_updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS memory_jobs (
                    kind TEXT NOT NULL,
                    job_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    ownership_token TEXT,
                    source_updated_at TEXT,
                    lease_until REAL,
                    retry_at REAL,
                    input_watermark INTEGER NOT NULL DEFAULT 0,
                    completed_watermark INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(kind, job_key)
                );
                CREATE INDEX IF NOT EXISTS memory_stage1_usage
                    ON memory_stage1_outputs(
                        usage_count DESC, last_used_at DESC, generated_at DESC
                    );
                """
            )
            # executescript ends the previous transaction. Serialize additive
            # migrations again so concurrent openers cannot race ALTER TABLE.
            connection.execute("BEGIN IMMEDIATE")
            job_columns = {row[1] for row in connection.execute("PRAGMA table_info(memory_jobs)")}
            if "retry_remaining" not in job_columns:
                # Older databases have no failure count. Preserve their backoff
                # and owner; start the newly enforceable budget at three.
                connection.execute(
                    "ALTER TABLE memory_jobs ADD COLUMN retry_remaining INTEGER NOT NULL DEFAULT 3"
                )
            if "last_success_source_updated_at" not in job_columns:
                connection.execute(
                    "ALTER TABLE memory_jobs ADD COLUMN last_success_source_updated_at TEXT"
                )
                connection.execute(
                    "UPDATE memory_jobs SET last_success_source_updated_at=source_updated_at "
                    "WHERE kind='memory_stage1' AND status='succeeded'"
                )
            if "finished_at" not in job_columns:
                # updated_at may describe an enqueue, not a successful finish.
                # Old databases provide no reliable cooldown start time.
                connection.execute("ALTER TABLE memory_jobs ADD COLUMN finished_at REAL")

    async def claim_extraction_jobs(
        self,
        *,
        current_thread_id: ThreadId,
        max_age_days: int,
        min_idle_hours: int,
        limit: int,
        lease_seconds: int,
    ) -> tuple[MemoryExtractionClaim, ...]:
        return await asyncio.to_thread(
            self._claim_extraction_jobs,
            current_thread_id,
            max_age_days,
            min_idle_hours,
            limit,
            lease_seconds,
        )

    def _claim_extraction_jobs(
        self,
        current_thread_id: ThreadId,
        max_age_days: int,
        min_idle_hours: int,
        limit: int,
        lease_seconds: int,
    ) -> tuple[MemoryExtractionClaim, ...]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return claim_extraction_jobs(
                connection,
                current_thread_id=current_thread_id,
                max_age_days=max_age_days,
                min_idle_hours=min_idle_hours,
                limit=limit,
                lease_seconds=lease_seconds,
                now=time.time(),
            )

    async def complete_extraction(
        self, claim: MemoryExtractionClaim, memory: StageOneMemory | None
    ) -> bool:
        return await asyncio.to_thread(self._complete_extraction, claim, memory)

    def _complete_extraction(
        self, claim: MemoryExtractionClaim, memory: StageOneMemory | None
    ) -> bool:
        if memory is not None and (
            memory.thread_id != claim.thread_id
            or memory.source_updated_at != claim.source_updated_at
            or memory.cwd != claim.cwd
        ):
            raise ValueError("stage-one memory does not match its extraction claim")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            owned = connection.execute(
                """
                SELECT thread.memory_mode, thread.updated_at
                FROM memory_jobs AS job
                JOIN threads AS thread ON thread.id=job.job_key
                WHERE job.kind=? AND job.job_key=?
                  AND job.status='running' AND job.ownership_token=?
                """,
                (_STAGE_ONE, str(claim.thread_id), claim.ownership_token),
            ).fetchone()
            if owned is None:
                return False
            if owned["memory_mode"] != "enabled" or owned["updated_at"] != claim.source_updated_at:
                # The source changed while the model was sampling, or an
                # external-context policy invalidated it. Release the lease
                # without publishing stale/polluted output. An enabled newer
                # source is immediately claimable on the next bounded pass.
                connection.execute(
                    """
                    UPDATE memory_jobs SET status=?, ownership_token=NULL,
                        lease_until=NULL, retry_at=NULL, error=NULL,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE kind=? AND job_key=? AND ownership_token=?
                    """,
                    (
                        "pending" if owned["memory_mode"] == "enabled" else "succeeded",
                        _STAGE_ONE,
                        str(claim.thread_id),
                        claim.ownership_token,
                    ),
                )
                return False
            changed = memory is not None
            if memory is None:
                changed = (
                    connection.execute(
                        "DELETE FROM memory_stage1_outputs WHERE thread_id=?",
                        (str(claim.thread_id),),
                    ).rowcount
                    > 0
                )
            else:
                connection.execute(
                    """
                    INSERT INTO memory_stage1_outputs(
                        thread_id, source_updated_at, cwd, raw_memory,
                        rollout_summary, rollout_slug, generated_at,
                        usage_count, last_used_at, selected_for_phase2,
                        selected_source_updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 0, NULL, 0, NULL)
                    ON CONFLICT(thread_id) DO UPDATE SET
                        source_updated_at=excluded.source_updated_at,
                        cwd=excluded.cwd,
                        raw_memory=excluded.raw_memory,
                        rollout_summary=excluded.rollout_summary,
                        rollout_slug=excluded.rollout_slug,
                        generated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        str(memory.thread_id),
                        memory.source_updated_at,
                        str(memory.cwd),
                        memory.raw_memory,
                        memory.rollout_summary,
                        memory.rollout_slug,
                    ),
                )
            if changed:
                enqueue_consolidation(connection, time.time_ns())
            connection.execute(
                """
                UPDATE memory_jobs SET status='succeeded', ownership_token=NULL,
                    lease_until=NULL, retry_at=NULL, error=NULL, updated_at=CURRENT_TIMESTAMP,
                    last_success_source_updated_at=source_updated_at
                WHERE kind=? AND job_key=? AND ownership_token=?
                """,
                (_STAGE_ONE, str(claim.thread_id), claim.ownership_token),
            )
        return True

    async def fail_extraction(
        self,
        claim: MemoryExtractionClaim,
        error: str,
        *,
        retry_delay_seconds: int,
    ) -> bool:
        return await asyncio.to_thread(
            self._fail_job,
            _STAGE_ONE,
            str(claim.thread_id),
            claim.ownership_token,
            error,
            retry_delay_seconds,
        )

    def _fail_job(
        self,
        kind: str,
        key: str,
        token: str,
        error: str,
        retry_delay_seconds: int,
    ) -> bool:
        now = int(time.time())
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE memory_jobs SET status='failed', ownership_token=NULL,
                    lease_until=NULL, retry_at=?, error=?, updated_at=CURRENT_TIMESTAMP,
                    retry_remaining=CASE WHEN kind='memory_stage1'
                        THEN retry_remaining-1 ELSE MAX(retry_remaining-1, 0) END,
                    finished_at=CASE WHEN kind='memory_consolidate_global'
                        THEN ? ELSE finished_at END
                WHERE kind=? AND job_key=? AND status='running' AND ownership_token=?
                """,
                (now + retry_delay_seconds, error[:4_000], now, kind, key, token),
            )
        return cursor.rowcount == 1

    async def claim_consolidation(self, *, lease_seconds: int) -> ConsolidationClaim | None:
        return await asyncio.to_thread(self._claim_consolidation, lease_seconds)

    async def heartbeat_consolidation(
        self, claim: ConsolidationClaim, *, lease_seconds: int
    ) -> bool:
        return await _joined_write(lambda: self._heartbeat_consolidation(claim, lease_seconds))

    def _heartbeat_consolidation(self, claim: ConsolidationClaim, lease_seconds: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE memory_jobs SET lease_until=? WHERE kind=? AND job_key=? "
                "AND status='running' AND ownership_token=?",
                (time.time() + lease_seconds, _CONSOLIDATE, _GLOBAL_KEY, claim.ownership_token),
            )
        return cursor.rowcount == 1

    async def write_consolidation_workspace(
        self, claim: ConsolidationClaim, write: Callable[[], None]
    ) -> bool:
        return await _joined_write(lambda: self._write_consolidation_workspace(claim, write))

    def _write_consolidation_workspace(
        self, claim: ConsolidationClaim, write: Callable[[], None]
    ) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT 1 FROM memory_jobs WHERE kind=? AND job_key=? "
                "AND status='running' AND ownership_token=?",
                (_CONSOLIDATE, _GLOBAL_KEY, claim.ownership_token),
            ).fetchone()
            if row is None:
                return False
            write()
        return True

    async def enqueue_consolidation(self, *, force: bool = False) -> bool:
        """Record an input change; force is retained for older caller compatibility."""
        del force
        return await asyncio.to_thread(self._enqueue_consolidation)

    def _enqueue_consolidation(self) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            enqueue_consolidation(connection, time.time_ns())
        return True

    def _claim_consolidation(self, lease_seconds: int) -> ConsolidationClaim | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return claim_consolidation(connection, lease_seconds=lease_seconds, now=time.time())

    async def load_consolidation_inputs(
        self, *, limit: int, max_unused_days: int
    ) -> tuple[StageOneMemory, ...]:
        return await asyncio.to_thread(self._load_consolidation_inputs, limit, max_unused_days)

    def _load_consolidation_inputs(
        self, limit: int, max_unused_days: int
    ) -> tuple[StageOneMemory, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM (
                    SELECT output.* FROM memory_stage1_outputs AS output
                    JOIN threads AS thread ON thread.id=output.thread_id
                    WHERE thread.memory_mode='enabled'
                      AND (length(trim(raw_memory)) > 0 OR length(trim(rollout_summary)) > 0)
                      AND julianday(COALESCE(last_used_at, source_updated_at)) >= julianday(?)
                    ORDER BY COALESCE(usage_count, 0) DESC,
                             julianday(COALESCE(last_used_at, source_updated_at)) DESC,
                             julianday(source_updated_at) DESC,
                             thread_id DESC
                    LIMIT ?
                ) ORDER BY thread_id ASC
                """,
                (_retention_cutoff(max_unused_days), max(0, limit)),
            ).fetchall()
        return tuple(_stage_one_from_row(row) for row in rows)

    async def prune_stage_one_outputs(self, *, max_unused_days: int, limit: int) -> int:
        """Prune old inputs without invalidating a successful baseline or resampling watermarks."""
        return await _joined_write(lambda: self._prune_stage_one_outputs(max_unused_days, limit))

    def _prune_stage_one_outputs(self, max_unused_days: int, limit: int) -> int:
        if limit <= 0:
            return 0
        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM memory_stage1_outputs WHERE thread_id IN (
                    SELECT thread_id FROM memory_stage1_outputs
                    WHERE selected_for_phase2=0
                      AND julianday(COALESCE(last_used_at, source_updated_at)) < julianday(?)
                    ORDER BY julianday(COALESCE(last_used_at, source_updated_at)) ASC,
                             julianday(source_updated_at) ASC, thread_id ASC
                    LIMIT ?
                )
                """,
                (_retention_cutoff(max_unused_days), limit),
            )
        return cursor.rowcount

    async def complete_consolidation(
        self,
        claim: ConsolidationClaim,
        selected: tuple[StageOneMemory, ...],
        *,
        publish: Callable[[], None] | None = None,
    ) -> bool:
        return await _joined_write(lambda: self._complete_consolidation(claim, selected, publish))

    def _complete_consolidation(
        self,
        claim: ConsolidationClaim,
        selected: tuple[StageOneMemory, ...],
        publish: Callable[[], None] | None = None,
    ) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT input_watermark FROM memory_jobs WHERE kind=? AND job_key=? "
                "AND status='running' AND ownership_token=?",
                (_CONSOLIDATE, _GLOBAL_KEY, claim.ownership_token),
            ).fetchone()
            if row is None:
                return False
            # This lock fences all cooperative owners through publication and
            # DB commit. It is not a cross-filesystem/SQLite crash transaction.
            if publish is not None:
                publish()
            connection.execute(
                "UPDATE memory_stage1_outputs SET selected_for_phase2=0, "
                "selected_source_updated_at=NULL"
            )
            for memory in selected:
                connection.execute(
                    """
                    UPDATE memory_stage1_outputs SET selected_for_phase2=1,
                        selected_source_updated_at=? WHERE thread_id=? AND source_updated_at=?
                    """,
                    (memory.source_updated_at, str(memory.thread_id), memory.source_updated_at),
                )
            connection.execute(
                """
                UPDATE memory_jobs SET status='succeeded', ownership_token=NULL, lease_until=NULL,
                    retry_at=NULL, error=NULL,
                    completed_watermark=MAX(completed_watermark, ?),
                    finished_at=?, updated_at=CURRENT_TIMESTAMP
                WHERE kind=? AND job_key=? AND ownership_token=?
                """,
                (
                    claim.input_watermark,
                    int(time.time()),
                    _CONSOLIDATE,
                    _GLOBAL_KEY,
                    claim.ownership_token,
                ),
            )
        return True

    async def fail_consolidation(
        self,
        claim: ConsolidationClaim,
        error: str,
        *,
        retry_delay_seconds: int,
    ) -> bool:
        return await asyncio.to_thread(
            self._fail_job,
            _CONSOLIDATE,
            _GLOBAL_KEY,
            claim.ownership_token,
            error,
            retry_delay_seconds,
        )

    async def mark_thread_mode(self, thread_id: ThreadId, mode: str) -> None:
        if mode not in {"enabled", "disabled", "polluted"}:
            raise ValueError(f"unsupported thread memory mode: {mode}")
        await _joined_write(lambda: self._mark_thread_mode(thread_id, mode))

    def _mark_thread_mode(self, thread_id: ThreadId, mode: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE threads SET memory_mode=? WHERE id=?", (mode, str(thread_id))
            )
            if mode == "polluted":
                selected = connection.execute(
                    "SELECT selected_for_phase2 FROM memory_stage1_outputs WHERE thread_id=?",
                    (str(thread_id),),
                ).fetchone()
                if selected is not None and selected["selected_for_phase2"]:
                    # Repeat even when already polluted: the previous successful
                    # baseline still needs reconciliation until phase two replaces it.
                    # Preserve an active owner's lease and the ordinary cooldown.
                    enqueue_consolidation(connection, time.time_ns())

    async def mark_memories_used(self, thread_ids: Iterable[ThreadId]) -> None:
        signals = tuple(str(value) for value in thread_ids)
        if signals:
            await asyncio.to_thread(self._mark_memories_used, signals)

    def _mark_memories_used(self, thread_ids: tuple[str, ...]) -> None:
        now = datetime.fromtimestamp(time.time(), UTC).isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.executemany(
                """
                UPDATE memory_stage1_outputs SET usage_count=COALESCE(usage_count, 0)+1,
                    last_used_at=? WHERE thread_id=?
                """,
                ((now, thread_id) for thread_id in thread_ids),
            )

    async def close(self) -> None:
        return None


def _retention_cutoff(max_unused_days: int) -> str:
    return (
        datetime.fromtimestamp(time.time(), UTC) - timedelta(days=max(0, max_unused_days))
    ).isoformat(timespec="seconds")


async def _joined_write(write: Callable[[], _WriteResult]) -> _WriteResult:
    """Cancelling to_thread cannot stop its worker; retain ownership until exit."""
    task = asyncio.create_task(asyncio.to_thread(write))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:  # noqa: BLE001 - preserve cancellation after joining worker
                break
        if not task.cancelled() and (error := task.exception()) is not None:
            logging.getLogger(__name__).warning(
                "Memory write failed during cancellation: %s", error
            )
        raise


def _stage_one_from_row(row: sqlite3.Row) -> StageOneMemory:
    return StageOneMemory(
        thread_id=ThreadId(row["thread_id"]),
        cwd=Path(row["cwd"]),
        source_updated_at=row["source_updated_at"],
        raw_memory=row["raw_memory"],
        rollout_summary=row["rollout_summary"],
        rollout_slug=row["rollout_slug"],
        usage_count=row["usage_count"],
        last_used_at=row["last_used_at"],
    )
