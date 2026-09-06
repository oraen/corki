"""Transactional SQLite job store for long-term memory generation.

The main session repository owns conversation writes.  This adapter shares the
same database file but owns only memory-specific tables and the thread memory
mode column, mirroring Codex's separation between thread state and memories.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path
from uuid import uuid4

from corki.memory.models import ConsolidationClaim, MemoryExtractionClaim, StageOneMemory
from corki.protocol.ids import ThreadId
from corki.protocol.items import item_from_payload

_STAGE_ONE = "memory_stage1"
_CONSOLIDATE = "memory_consolidate_global"
_GLOBAL_KEY = "global"


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
        now = time.time()
        claims: list[MemoryExtractionClaim] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            candidates = connection.execute(
                """
                SELECT t.id, t.cwd, t.updated_at
                FROM threads AS t
                WHERE t.id != ?
                  AND t.memory_mode = 'enabled'
                  AND datetime(t.updated_at) >= datetime('now', ?)
                  AND datetime(t.updated_at) <= datetime('now', ?)
                  AND EXISTS (
                      SELECT 1 FROM turns
                      WHERE turns.thread_id=t.id AND turns.status='completed'
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM memory_stage1_outputs AS output
                      WHERE output.thread_id=t.id
                        AND output.source_updated_at=t.updated_at
                  )
                ORDER BY datetime(t.updated_at) DESC, t.id
                LIMIT ?
                """,
                (
                    str(current_thread_id),
                    f"-{max_age_days} days",
                    f"-{min_idle_hours} hours",
                    max(limit * 8, limit),
                ),
            ).fetchall()
            for row in candidates:
                if len(claims) >= limit:
                    break
                existing = connection.execute(
                    "SELECT status, lease_until, retry_at, source_updated_at "
                    "FROM memory_jobs WHERE kind=? AND job_key=?",
                    (_STAGE_ONE, row["id"]),
                ).fetchone()
                if existing is not None:
                    same_source = existing["source_updated_at"] == row["updated_at"]
                    if same_source and existing["status"] == "succeeded":
                        continue
                    if existing["status"] == "running" and (existing["lease_until"] or 0) > now:
                        continue
                    if (
                        same_source
                        and existing["status"] == "failed"
                        and (existing["retry_at"] or 0) > now
                    ):
                        continue
                token = str(uuid4())
                connection.execute(
                    """
                    INSERT INTO memory_jobs(
                        kind, job_key, status, ownership_token, source_updated_at,
                        lease_until, retry_at, error
                    ) VALUES (?, ?, 'running', ?, ?, ?, NULL, NULL)
                    ON CONFLICT(kind, job_key) DO UPDATE SET
                        status='running', ownership_token=excluded.ownership_token,
                        source_updated_at=excluded.source_updated_at,
                        lease_until=excluded.lease_until, retry_at=NULL, error=NULL,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (_STAGE_ONE, row["id"], token, row["updated_at"], now + lease_seconds),
                )
                item_rows = connection.execute(
                    "SELECT kind, payload_json FROM conversation_items "
                    "WHERE thread_id=? ORDER BY sequence",
                    (row["id"],),
                ).fetchall()
                items = tuple(
                    item_from_payload(item["kind"], json.loads(item["payload_json"]))
                    for item in item_rows
                )
                claims.append(
                    MemoryExtractionClaim(
                        ThreadId(row["id"]),
                        Path(row["cwd"]),
                        row["updated_at"],
                        token,
                        items,
                    )
                )
        return tuple(claims)

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
            if memory is not None:
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
                        generated_at=CURRENT_TIMESTAMP,
                        selected_for_phase2=0
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
                watermark = time.time_ns()
                connection.execute(
                    """
                    INSERT INTO memory_jobs(kind, job_key, status, input_watermark)
                    VALUES (?, ?, 'pending', ?)
                    ON CONFLICT(kind, job_key) DO UPDATE SET
                        status=CASE WHEN memory_jobs.status='running'
                                    THEN memory_jobs.status ELSE 'pending' END,
                        input_watermark=MAX(memory_jobs.input_watermark, excluded.input_watermark),
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (_CONSOLIDATE, _GLOBAL_KEY, watermark),
                )
            connection.execute(
                """
                UPDATE memory_jobs SET status='succeeded', ownership_token=NULL,
                    lease_until=NULL, retry_at=NULL, error=NULL, updated_at=CURRENT_TIMESTAMP
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
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE memory_jobs SET status='failed', ownership_token=NULL,
                    lease_until=NULL, retry_at=?, error=?, updated_at=CURRENT_TIMESTAMP
                WHERE kind=? AND job_key=? AND status='running' AND ownership_token=?
                """,
                (time.time() + retry_delay_seconds, error[:4_000], kind, key, token),
            )
        return cursor.rowcount == 1

    async def claim_consolidation(self, *, lease_seconds: int) -> ConsolidationClaim | None:
        return await asyncio.to_thread(self._claim_consolidation, lease_seconds)

    async def enqueue_consolidation(self, *, force: bool = False) -> bool:
        return await asyncio.to_thread(self._enqueue_consolidation, force)

    def _enqueue_consolidation(self, force: bool) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if (
                not force
                and connection.execute("SELECT 1 FROM memory_stage1_outputs LIMIT 1").fetchone()
                is None
            ):
                return False
            watermark = time.time_ns()
            connection.execute(
                """
                INSERT INTO memory_jobs(kind, job_key, status, input_watermark)
                VALUES (?, ?, 'pending', ?)
                ON CONFLICT(kind, job_key) DO UPDATE SET
                    status=CASE WHEN memory_jobs.status='running'
                                THEN memory_jobs.status ELSE 'pending' END,
                    input_watermark=MAX(memory_jobs.input_watermark, excluded.input_watermark),
                    updated_at=CURRENT_TIMESTAMP
                """,
                (_CONSOLIDATE, _GLOBAL_KEY, watermark),
            )
        return True

    def _claim_consolidation(self, lease_seconds: int) -> ConsolidationClaim | None:
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM memory_jobs WHERE kind=? AND job_key=?",
                (_CONSOLIDATE, _GLOBAL_KEY),
            ).fetchone()
            if row is None or row["input_watermark"] <= row["completed_watermark"]:
                return None
            if row["status"] == "running" and (row["lease_until"] or 0) > now:
                return None
            if row["status"] == "failed" and (row["retry_at"] or 0) > now:
                return None
            token = str(uuid4())
            connection.execute(
                """
                UPDATE memory_jobs SET status='running', ownership_token=?, lease_until=?,
                    retry_at=NULL, error=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE kind=? AND job_key=?
                """,
                (token, now + lease_seconds, _CONSOLIDATE, _GLOBAL_KEY),
            )
            return ConsolidationClaim(token, row["input_watermark"])

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
                SELECT * FROM memory_stage1_outputs
                WHERE datetime(COALESCE(last_used_at, generated_at)) >= datetime('now', ?)
                ORDER BY usage_count DESC,
                         datetime(COALESCE(last_used_at, generated_at)) DESC,
                         thread_id
                LIMIT ?
                """,
                (f"-{max_unused_days} days", limit),
            ).fetchall()
        return tuple(_stage_one_from_row(row) for row in rows)

    async def complete_consolidation(
        self,
        claim: ConsolidationClaim,
        selected: tuple[StageOneMemory, ...],
    ) -> bool:
        return await asyncio.to_thread(self._complete_consolidation, claim, selected)

    def _complete_consolidation(
        self, claim: ConsolidationClaim, selected: tuple[StageOneMemory, ...]
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
            connection.execute("UPDATE memory_stage1_outputs SET selected_for_phase2=0")
            for memory in selected:
                connection.execute(
                    """
                    UPDATE memory_stage1_outputs SET selected_for_phase2=1,
                        selected_source_updated_at=? WHERE thread_id=?
                    """,
                    (memory.source_updated_at, str(memory.thread_id)),
                )
            pending = row["input_watermark"] > claim.input_watermark
            connection.execute(
                """
                UPDATE memory_jobs SET status=?, ownership_token=NULL, lease_until=NULL,
                    retry_at=NULL, error=NULL,
                    completed_watermark=MAX(completed_watermark, ?),
                    updated_at=CURRENT_TIMESTAMP
                WHERE kind=? AND job_key=? AND ownership_token=?
                """,
                (
                    "pending" if pending else "succeeded",
                    claim.input_watermark,
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
        await asyncio.to_thread(self._mark_thread_mode, thread_id, mode)

    def _mark_thread_mode(self, thread_id: ThreadId, mode: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE threads SET memory_mode=? WHERE id=?", (mode, str(thread_id))
            )

    async def mark_memories_used(self, thread_ids: Iterable[ThreadId]) -> None:
        unique = tuple(dict.fromkeys(str(value) for value in thread_ids))
        if unique:
            await asyncio.to_thread(self._mark_memories_used, unique)

    def _mark_memories_used(self, thread_ids: tuple[str, ...]) -> None:
        with self._connect() as connection:
            connection.executemany(
                """
                UPDATE memory_stage1_outputs SET usage_count=usage_count+1,
                    last_used_at=CURRENT_TIMESTAMP WHERE thread_id=?
                """,
                ((thread_id,) for thread_id in thread_ids),
            )

    async def close(self) -> None:
        return None


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
