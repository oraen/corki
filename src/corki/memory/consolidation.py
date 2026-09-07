"""Global phase-two claim and enqueue policy; workspace dirtiness is checked later."""

from __future__ import annotations

import sqlite3
from uuid import uuid4

from corki.memory.models import ConsolidationClaim

SUCCESS_COOLDOWN_SECONDS = 6 * 60 * 60


def enqueue_consolidation(connection: sqlite3.Connection, input_watermark: int) -> None:
    """Record a real input change without erasing ownership or successful cooldown."""
    connection.execute(
        """
        INSERT INTO memory_jobs(kind, job_key, status, input_watermark, retry_remaining)
        VALUES ('memory_consolidate_global', 'global', 'pending', ?, 3)
        ON CONFLICT(kind, job_key) DO UPDATE SET
            status=CASE WHEN memory_jobs.status='running' THEN 'running' ELSE 'pending' END,
            retry_at=CASE WHEN memory_jobs.status='running' THEN memory_jobs.retry_at END,
            retry_remaining=MAX(memory_jobs.retry_remaining, excluded.retry_remaining),
            input_watermark=CASE WHEN excluded.input_watermark > memory_jobs.input_watermark
                THEN excluded.input_watermark ELSE memory_jobs.input_watermark+1 END,
            updated_at=CURRENT_TIMESTAMP
        """,
        (input_watermark,),
    )


def claim_consolidation(
    connection: sqlite3.Connection, *, lease_seconds: int, now: float
) -> ConsolidationClaim | None:
    """Claim under the caller's BEGIN IMMEDIATE, independent of DB watermarks."""
    row = connection.execute(
        "SELECT * FROM memory_jobs WHERE kind='memory_consolidate_global' AND job_key='global'"
    ).fetchone()
    now = int(now)
    token = str(uuid4())
    if row is None:
        connection.execute(
            "INSERT INTO memory_jobs(kind,job_key,status,ownership_token,lease_until) "
            "VALUES ('memory_consolidate_global','global','running',?,?)",
            (token, now + max(0, lease_seconds)),
        )
        return ConsolidationClaim(token, 0)
    if (row["retry_at"] or 0) > now:
        return None
    if row["status"] == "running" and (row["lease_until"] or 0) > now:
        return None
    if (
        row["error"] is None
        and row["finished_at"] is not None
        and row["finished_at"] > now - SUCCESS_COOLDOWN_SECONDS
    ):
        return None
    connection.execute(
        "UPDATE memory_jobs SET status='running',ownership_token=?,lease_until=?,"
        "retry_at=NULL,error=NULL,finished_at=NULL,updated_at=CURRENT_TIMESTAMP "
        "WHERE kind='memory_consolidate_global' AND job_key='global'",
        (token, now + max(0, lease_seconds)),
    )
    return ConsolidationClaim(token, row["input_watermark"])
