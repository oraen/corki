"""Stage-one source selection and claim admission inside the caller's transaction."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from corki.memory.models import MemoryExtractionClaim
from corki.memory.source_version import source_version_at_least, source_version_newer
from corki.protocol.ids import ThreadId
from corki.protocol.items import item_from_payload

THREAD_SCAN_LIMIT = 5_000
DEFAULT_RETRY_REMAINING = 3


def claim_extraction_jobs(
    connection: sqlite3.Connection,
    *,
    current_thread_id: ThreadId,
    max_age_days: int,
    min_idle_hours: int,
    limit: int,
    lease_seconds: int,
    now: float,
) -> tuple[MemoryExtractionClaim, ...]:
    """Bound source scanning before memory probes, sharing capacity across workers.

    The caller must hold BEGIN IMMEDIATE through the history read and commit.
    Source versions retain the existing durable text representation; the shared
    SQLite time key normalizes legacy CURRENT_TIMESTAMP and current ISO timestamps
    without rounding away submillisecond updates.
    """

    if limit <= 0:
        return ()
    running = connection.execute(
        "SELECT COUNT(*) FROM memory_jobs WHERE kind='memory_stage1' "
        "AND status='running' AND lease_until > ?",
        (now,),
    ).fetchone()[0]
    capacity = max(0, limit - running)
    if not capacity:
        return ()
    instant = datetime.fromtimestamp(now, UTC)
    candidates = connection.execute(
        """
        SELECT id, cwd, updated_at FROM threads
        WHERE id != ? AND memory_mode='enabled' AND preview != '' AND archived_at IS NULL
          AND source IN ('cli', 'vscode', 'atlas', 'chatgpt')
          AND source_version_epoch_micros(updated_at) >= source_version_epoch_micros(?)
          AND source_version_epoch_micros(updated_at) <= source_version_epoch_micros(?)
        ORDER BY source_version_epoch_micros(updated_at) DESC, id
        LIMIT ?
        """,
        (
            str(current_thread_id),
            (instant - timedelta(days=max(0, max_age_days))).isoformat(timespec="microseconds"),
            (instant - timedelta(hours=max(0, min_idle_hours))).isoformat(timespec="microseconds"),
            THREAD_SCAN_LIMIT,
        ),
    ).fetchall()
    claims: list[MemoryExtractionClaim] = []
    for row in candidates:
        if len(claims) >= capacity:
            break
        output = connection.execute(
            "SELECT source_updated_at FROM memory_stage1_outputs WHERE thread_id=?",
            (row["id"],),
        ).fetchone()
        if output is not None and source_version_at_least(
            output["source_updated_at"], row["updated_at"]
        ):
            continue
        existing = connection.execute(
            "SELECT * FROM memory_jobs WHERE kind='memory_stage1' AND job_key=?",
            (row["id"],),
        ).fetchone()
        retries = DEFAULT_RETRY_REMAINING
        if existing is not None:
            succeeded = existing["last_success_source_updated_at"]
            if succeeded is None and existing["status"] == "succeeded":
                succeeded = existing["source_updated_at"]
            if succeeded and source_version_at_least(succeeded, row["updated_at"]):
                continue
            if existing["status"] == "running" and (existing["lease_until"] or 0) > now:
                continue
            advanced = not existing["source_updated_at"] or source_version_newer(
                row["updated_at"], existing["source_updated_at"]
            )
            if not advanced:
                if (existing["retry_at"] or 0) > now or existing["retry_remaining"] <= 0:
                    continue
                retries = existing["retry_remaining"]
        token = str(uuid4())
        connection.execute(
            """
            INSERT INTO memory_jobs(
                kind, job_key, status, ownership_token, source_updated_at,
                lease_until, retry_remaining, retry_at, error
            ) VALUES ('memory_stage1', ?, 'running', ?, ?, ?, ?, NULL, NULL)
            ON CONFLICT(kind, job_key) DO UPDATE SET
                status='running', ownership_token=excluded.ownership_token,
                source_updated_at=excluded.source_updated_at,
                lease_until=excluded.lease_until, retry_remaining=excluded.retry_remaining,
                retry_at=NULL, error=NULL, updated_at=CURRENT_TIMESTAMP
            """,
            (row["id"], token, row["updated_at"], now + max(0, lease_seconds), retries),
        )
        item_rows = connection.execute(
            "SELECT kind, payload_json FROM conversation_items WHERE thread_id=? ORDER BY sequence",
            (row["id"],),
        ).fetchall()
        claims.append(
            MemoryExtractionClaim(
                ThreadId(row["id"]),
                Path(row["cwd"]),
                row["updated_at"],
                token,
                tuple(
                    item_from_payload(item["kind"], json.loads(item["payload_json"]))
                    for item in item_rows
                ),
            )
        )
    return tuple(claims)
