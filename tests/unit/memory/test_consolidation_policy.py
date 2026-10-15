"""Phase-two locking is distinct from workspace dirtiness and stage-one retry limits."""

import asyncio
import sqlite3
import time

import pytest

from corki.memory import SQLiteMemoryRepository
from corki.storage import SQLiteSessionRepository


@pytest.fixture
def store(tmp_path, monkeypatch):
    database = tmp_path / "sessions.db"
    SQLiteSessionRepository(database)
    now = [time.time()]
    monkeypatch.setattr("corki.memory.sqlite.time.time", lambda: now[0])
    return database, SQLiteMemoryRepository(database), now


def test_missing_row_is_claimable_and_shared_lock_prevents_second_runner(store):
    database, repository, _ = store

    async def scenario():
        first = await repository.claim_consolidation(lease_seconds=60)
        assert first is not None and first.input_watermark == 0
        assert await SQLiteMemoryRepository(database).claim_consolidation(lease_seconds=60) is None
        assert await repository.complete_consolidation(first, ())
        assert await repository.claim_consolidation(lease_seconds=60) is None

    asyncio.run(scenario())


def test_success_cooldown_includes_new_input_and_equal_watermark_is_not_a_dirty_gate(store):
    database, repository, now = store

    async def scenario():
        await repository.enqueue_consolidation(force=True)
        first = await repository.claim_consolidation(lease_seconds=60)
        assert first is not None
        assert await repository.complete_consolidation(first, ())
        await repository.enqueue_consolidation(force=True)
        assert await repository.claim_consolidation(lease_seconds=60) is None
        now[0] += 6 * 3600
        second = await SQLiteMemoryRepository(database).claim_consolidation(lease_seconds=60)
        assert second is not None and second.input_watermark > first.input_watermark
        assert await repository.complete_consolidation(second, ())
        now[0] += 6 * 3600 - 1
        assert await repository.claim_consolidation(lease_seconds=60) is None
        now[0] += 1
        third = await repository.claim_consolidation(lease_seconds=60)
        assert third is not None and third.input_watermark == second.input_watermark

    asyncio.run(scenario())


def test_failure_backoff_is_status_independent_and_new_input_can_reset_it(store):
    database, repository, _ = store

    async def scenario():
        await repository.enqueue_consolidation(force=True)
        first = await repository.claim_consolidation(lease_seconds=60)
        assert first is not None
        assert await repository.fail_consolidation(first, "temporary", retry_delay_seconds=60)
        # Claim must respect persisted retry_at, not infer eligibility from a status label.
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE memory_jobs SET status='pending'")
        assert await repository.claim_consolidation(lease_seconds=60) is None
        await repository.enqueue_consolidation(force=True)
        replacement = await repository.claim_consolidation(lease_seconds=60)
        assert replacement is not None
        assert not await repository.complete_consolidation(first, ())
        assert not await repository.fail_consolidation(
            first, "late failure", retry_delay_seconds=60
        )

    asyncio.run(scenario())


def test_repeated_enqueue_strictly_advances_watermark_even_if_clock_does_not(store, monkeypatch):
    database, repository, _ = store
    monkeypatch.setattr("corki.memory.sqlite.time.time_ns", lambda: 10)

    async def scenario():
        await repository.enqueue_consolidation(force=True)
        await repository.enqueue_consolidation(force=True)
        first = await repository.claim_consolidation(lease_seconds=60)
        assert first is not None and first.input_watermark == 11
        await repository.enqueue_consolidation(force=True)
        assert await repository.claim_consolidation(lease_seconds=60) is None
        assert await repository.complete_consolidation(first, ())
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT input_watermark,completed_watermark,status FROM memory_jobs"
            ).fetchone()
        assert row == (12, 11, "succeeded")

    asyncio.run(scenario())


def test_expired_takeover_is_fenced_and_phase2_failures_do_not_exhaust_claims(store):
    _, repository, now = store

    async def scenario():
        first = await repository.claim_consolidation(lease_seconds=10)
        assert first is not None
        now[0] += 11
        for _ in range(5):
            owned = await repository.claim_consolidation(lease_seconds=10)
            assert owned is not None and owned.ownership_token != first.ownership_token
            assert not await repository.complete_consolidation(first, ())
            assert await repository.fail_consolidation(owned, "temporary", retry_delay_seconds=1)
            assert await repository.claim_consolidation(lease_seconds=10) is None
            now[0] += 1
        assert await repository.claim_consolidation(lease_seconds=10) is not None

    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["succeeded", "pending", "running", "failed"])
def test_legacy_phase2_migration_does_not_invent_success_time(tmp_path, status):
    database = tmp_path / "legacy.db"
    SQLiteSessionRepository(database)
    future = time.time() + 3600
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE memory_jobs (
                kind TEXT NOT NULL, job_key TEXT NOT NULL, status TEXT NOT NULL,
                ownership_token TEXT, source_updated_at TEXT, lease_until REAL, retry_at REAL,
                input_watermark INTEGER NOT NULL DEFAULT 0,
                completed_watermark INTEGER NOT NULL DEFAULT 0, error TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                retry_remaining INTEGER NOT NULL DEFAULT 3,
                last_success_source_updated_at TEXT,
                PRIMARY KEY(kind,job_key)
            )
            """
        )
        connection.execute(
            "INSERT INTO memory_jobs(kind,job_key,status,ownership_token,lease_until,"
            "retry_at,input_watermark,completed_watermark,error) "
            "VALUES ('memory_consolidate_global','global',?,?,?,?,7,7,?)",
            (
                status,
                "legacy-owner" if status == "running" else None,
                future if status == "running" else None,
                future if status == "failed" else None,
                "old failure" if status == "failed" else None,
            ),
        )
        before = connection.execute("SELECT * FROM memory_jobs").fetchone()
    repository = SQLiteMemoryRepository(database)
    with sqlite3.connect(database) as connection:
        after = connection.execute("SELECT * FROM memory_jobs").fetchone()
        assert after[:-1] == before and after[-1] is None

    async def scenario():
        claim = await repository.claim_consolidation(lease_seconds=60)
        if status in {"running", "failed"}:
            assert claim is None
        else:
            assert claim is not None and claim.input_watermark == 7

    asyncio.run(scenario())
