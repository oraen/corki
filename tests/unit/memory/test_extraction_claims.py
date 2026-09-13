"""Map pinned Codex stage-one claim and no-output contracts to real SQLite."""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from corki.memory import SQLiteMemoryRepository, StageOneMemory
from corki.protocol.ids import ThreadId, new_thread_id, new_turn_id
from corki.protocol.items import UserMessageItem
from corki.sessions import TurnRecord, TurnStatus
from corki.storage import SQLiteSessionRepository


async def seed(database, workspace, *, status=TurnStatus.COMPLETED):
    sessions = SQLiteSessionRepository(database)
    thread = new_thread_id()
    await sessions.create_thread(thread, workspace)
    await sessions.append_items(thread, (UserMessageItem("attempt", new_turn_id()),))
    if status is not None:
        await sessions.save_turn(TurnRecord(new_turn_id(), thread, status, "attempt"))
    return thread


async def claim(repository, *, limit=1, current=None, idle=0, lease=60):
    return await repository.claim_extraction_jobs(
        current_thread_id=current or new_thread_id(),
        max_age_days=10,
        min_idle_hours=idle,
        limit=limit,
        lease_seconds=lease,
    )


def set_version(database, thread, value):
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE threads SET updated_at=? WHERE id=?", (value, str(thread)))


@pytest.mark.parametrize("status", [None, TurnStatus.FAILED, TurnStatus.CANCELLED])
def test_source_does_not_require_a_successful_turn(tmp_path, status):
    async def scenario():
        database = tmp_path / "sessions.db"
        source = await seed(database, tmp_path, status=status)
        repository = SQLiteMemoryRepository(database)
        claims = await claim(repository)
        assert tuple(value.thread_id for value in claims) == (source,)
        assert not await claim(repository, current=source)

    asyncio.run(scenario())


def test_millisecond_age_idle_boundaries_and_order(tmp_path, monkeypatch):
    async def scenario():
        database = tmp_path / "sessions.db"
        now = datetime(2030, 1, 20, 12, 0, 0, 500_000, tzinfo=UTC)
        monkeypatch.setattr("corki.memory.sqlite.time.time", lambda: now.timestamp())
        offsets = [
            timedelta(hours=-6, milliseconds=1),  # too recent
            timedelta(hours=-6),  # inclusive idle boundary
            timedelta(hours=-6, milliseconds=-1),
            timedelta(days=-10),  # inclusive age boundary
            timedelta(days=-10, milliseconds=-1),  # too old
        ]
        sources = []
        for offset in offsets:
            source = await seed(database, tmp_path)
            set_version(database, source, (now + offset).isoformat(timespec="milliseconds"))
            sources.append(source)
        repository = SQLiteMemoryRepository(database)
        claims = await claim(repository, limit=10, idle=6)
        assert tuple(value.thread_id for value in claims) == tuple(sources[1:4])

    asyncio.run(scenario())


def test_global_running_cap_is_shared_by_independent_repositories(tmp_path):
    async def scenario():
        database = tmp_path / "sessions.db"
        for _ in range(8):
            await seed(database, tmp_path)
        first, second = SQLiteMemoryRepository(database), SQLiteMemoryRepository(database)
        batches = await asyncio.gather(claim(first, limit=2), claim(second, limit=2))
        active = batches[0] + batches[1]
        assert len(active) == 2
        assert len({value.thread_id for value in active}) == 2
        assert not await claim(first, limit=2)
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE memory_jobs SET lease_until=0")
        replacements = await claim(second, limit=2)
        assert len(replacements) == 2
        assert not await first.complete_extraction(active[0], None)

    asyncio.run(scenario())


@pytest.mark.parametrize("takeover", [False, True])
def test_expiry_allows_takeover_but_only_token_replacement_revokes_completion(
    tmp_path, monkeypatch, takeover
):
    async def scenario():
        database = tmp_path / "sessions.db"
        source = await seed(database, tmp_path)
        now = [datetime.now(UTC).timestamp()]
        monkeypatch.setattr("corki.memory.sqlite.time.time", lambda: now[0])
        version = datetime.fromtimestamp(now[0], UTC) - timedelta(hours=1)
        set_version(database, source, version.isoformat())
        first = SQLiteMemoryRepository(database)
        (old,) = await claim(first, lease=60)
        advanced = (version + timedelta(seconds=1)).isoformat()
        set_version(database, source, advanced)
        second = SQLiteMemoryRepository(database)
        assert not await claim(second, lease=60)  # New input does not steal an active lease.
        now[0] += 60  # Exact expiry, not an arbitrary time beyond the boundary.
        if takeover:
            (fresh,) = await claim(second, lease=60)
            assert fresh.source_updated_at == advanced
            assert fresh.ownership_token != old.ownership_token
        memory = StageOneMemory(
            source, tmp_path, old.source_updated_at, "old detail", "old summary"
        )
        assert await first.complete_extraction(old, memory) is (not takeover)
        if not takeover:
            (fresh,) = await claim(second, lease=60)
            assert fresh.source_updated_at == advanced
        assert await second.complete_extraction(
            fresh,
            StageOneMemory(source, tmp_path, fresh.source_updated_at, "new detail", "new summary"),
        )
        assert not await first.complete_extraction(old, memory)
        assert not await claim(SQLiteMemoryRepository(database))
        inputs = await second.load_consolidation_inputs(limit=10, max_unused_days=30)
        assert len(inputs) == 1 and inputs[0].raw_memory == "new detail"

    asyncio.run(scenario())


def test_three_failures_exhaust_source_but_newer_version_resets_budget(tmp_path):
    async def scenario():
        database = tmp_path / "sessions.db"
        source = await seed(database, tmp_path)
        version = datetime.now(UTC) - timedelta(hours=1)
        set_version(database, source, version.isoformat())
        repository = SQLiteMemoryRepository(database)
        for _ in range(3):
            (owned,) = await claim(repository)
            assert await repository.fail_extraction(owned, "bad extraction", retry_delay_seconds=0)
            assert not await repository.fail_extraction(
                owned, "late duplicate", retry_delay_seconds=0
            )
        assert not await claim(SQLiteMemoryRepository(database))
        set_version(database, source, (version - timedelta(seconds=1)).isoformat())
        assert not await claim(repository)  # merely different is not newer
        set_version(database, source, (version + timedelta(seconds=1)).isoformat())
        (fresh,) = await claim(repository)
        assert await repository.fail_extraction(fresh, "temporary", retry_delay_seconds=3600)
        assert not await claim(repository)
        set_version(database, source, (version + timedelta(seconds=2)).isoformat())
        (latest,) = await claim(repository)  # genuine advancement bypasses backoff
        assert await repository.complete_extraction(latest, None)
        assert not await claim(repository)

    asyncio.run(scenario())


@pytest.mark.parametrize("with_output", [False, True])
def test_success_watermark_blocks_equal_or_older_sources_after_reopen(tmp_path, with_output):
    async def scenario():
        database = tmp_path / "sessions.db"
        source = await seed(database, tmp_path)
        version = datetime.now(UTC) - timedelta(hours=1)
        set_version(database, source, version.isoformat())
        repository = SQLiteMemoryRepository(database)
        (owned,) = await claim(repository)
        memory = (
            StageOneMemory(source, tmp_path, owned.source_updated_at, "detail", "summary")
            if with_output
            else None
        )
        assert await repository.complete_extraction(owned, memory)
        repository = SQLiteMemoryRepository(database)
        assert not await claim(repository)
        set_version(database, source, (version - timedelta(seconds=1)).isoformat())
        assert not await claim(repository)

    asyncio.run(scenario())


def test_no_output_removes_old_extraction_and_enqueues_consolidation(tmp_path):
    async def scenario():
        database = tmp_path / "sessions.db"
        source = await seed(database, tmp_path)
        version = datetime.now(UTC) - timedelta(hours=1)
        set_version(database, source, version.isoformat())
        repository = SQLiteMemoryRepository(database)
        (owned,) = await claim(repository)
        output = StageOneMemory(
            source, tmp_path, owned.source_updated_at, "old detail", "old index"
        )
        assert await repository.complete_extraction(owned, output)
        phase2 = await repository.claim_consolidation(lease_seconds=60)
        assert phase2 is not None
        assert await repository.complete_consolidation(phase2, (output,))
        set_version(database, source, (version + timedelta(seconds=1)).isoformat())
        (replacement,) = await claim(repository)
        assert await repository.complete_extraction(replacement, None)
        assert await repository.load_consolidation_inputs(limit=10, max_unused_days=30) == ()
        assert await repository.claim_consolidation(lease_seconds=60) is None
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE memory_jobs SET finished_at=0 WHERE job_key='global'")
        pending = await repository.claim_consolidation(lease_seconds=60)
        assert pending is not None and pending.input_watermark > phase2.input_watermark
        assert await repository.complete_consolidation(pending, ())
        assert not await claim(SQLiteMemoryRepository(database))

    asyncio.run(scenario())


@pytest.mark.parametrize("prefix_count,has_output,expected", [(8, False, 1), (5000, True, 0)])
def test_bounded_scan_precedes_memory_staleness_probes(
    tmp_path, prefix_count, has_output, expected
):
    async def scenario():
        database = tmp_path / "sessions.db"
        SQLiteSessionRepository(database)
        repository = SQLiteMemoryRepository(database)
        now = datetime.now(UTC) - timedelta(hours=1)
        with sqlite3.connect(database) as connection:
            for index in range(prefix_count + 1):
                thread = f"source-{index:05}"
                updated = (now - timedelta(seconds=index)).isoformat()
                connection.execute(
                    "INSERT INTO threads(id, cwd, updated_at, preview) "
                    "VALUES (?, ?, ?, 'evidence')",
                    (thread, str(tmp_path), updated),
                )
                connection.execute(
                    "INSERT INTO turns(id, thread_id, status, user_input) "
                    "VALUES (?, ?, 'completed', '')",
                    (f"turn-{index}", thread),
                )
                if index < prefix_count:
                    connection.execute(
                        "INSERT INTO memory_jobs(kind, job_key, status, source_updated_at) "
                        "VALUES ('memory_stage1', ?, 'succeeded', ?)",
                        (thread, updated),
                    )
                    if has_output:
                        connection.execute(
                            "INSERT INTO memory_stage1_outputs(thread_id, source_updated_at, cwd, "
                            "raw_memory, rollout_summary) VALUES (?, ?, ?, 'detail', 'summary')",
                            (thread, updated, str(tmp_path)),
                        )
        claims = await claim(repository)
        assert len(claims) == expected
        if expected:
            assert claims[0].thread_id == ThreadId(f"source-{prefix_count:05}")

    asyncio.run(scenario())


def test_previous_success_watermark_survives_a_newer_failed_attempt(tmp_path):
    async def scenario():
        database = tmp_path / "sessions.db"
        source = await seed(database, tmp_path)
        version = datetime.now(UTC) - timedelta(hours=1)
        set_version(database, source, version.isoformat())
        repository = SQLiteMemoryRepository(database)
        (success,) = await claim(repository)
        assert await repository.complete_extraction(success, None)
        set_version(database, source, (version + timedelta(seconds=1)).isoformat())
        (newer,) = await claim(repository)
        assert await repository.fail_extraction(newer, "failed newer input", retry_delay_seconds=0)
        set_version(database, source, version.isoformat())
        assert not await claim(SQLiteMemoryRepository(database))

    asyncio.run(scenario())


def test_legacy_job_migration_preserves_owner_backoff_and_success(tmp_path):
    async def scenario():
        database = tmp_path / "sessions.db"
        sources = [await seed(database, tmp_path) for _ in range(3)]
        version = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        future = (datetime.now(UTC) + timedelta(hours=1)).timestamp()
        for source in sources:
            set_version(database, source, version)
        with sqlite3.connect(database) as connection:
            connection.execute(
                """
                CREATE TABLE memory_jobs (
                    kind TEXT NOT NULL, job_key TEXT NOT NULL, status TEXT NOT NULL,
                    ownership_token TEXT, source_updated_at TEXT, lease_until REAL,
                    retry_at REAL, input_watermark INTEGER NOT NULL DEFAULT 0,
                    completed_watermark INTEGER NOT NULL DEFAULT 0, error TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(kind, job_key)
                )
                """
            )
            for source, status, token, lease_until, retry_at in zip(
                sources,
                ("succeeded", "running", "failed"),
                (None, "old-owner", None),
                (None, future, None),
                (None, None, future),
                strict=True,
            ):
                connection.execute(
                    "INSERT INTO memory_jobs(kind, job_key, status, ownership_token, "
                    "source_updated_at, lease_until, retry_at) "
                    "VALUES ('memory_stage1', ?, ?, ?, ?, ?, ?)",
                    (source, status, token, version, lease_until, retry_at),
                )
            before = connection.execute("SELECT * FROM memory_jobs ORDER BY job_key").fetchall()
        repositories = await asyncio.gather(
            *(asyncio.to_thread(SQLiteMemoryRepository, database) for _ in range(4))
        )
        with sqlite3.connect(database) as connection:
            after = connection.execute("SELECT * FROM memory_jobs ORDER BY job_key").fetchall()
            assert [row[:-3] for row in after] == before
            assert all(row[-3] == 3 and row[-1] is None for row in after)
            assert {row[1]: row[-2] for row in after} == {
                sources[0]: version,
                sources[1]: None,
                sources[2]: None,
            }
        assert not await claim(repositories[0], limit=3)
        set_version(
            database,
            sources[0],
            (datetime.fromisoformat(version) - timedelta(seconds=1)).isoformat(),
        )
        assert not await claim(SQLiteMemoryRepository(database), limit=3)

    asyncio.run(scenario())
