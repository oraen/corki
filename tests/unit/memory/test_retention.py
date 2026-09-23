"""Source-based retention, visible Top-N and successful phase-two baselines."""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from corki.memory import SQLiteMemoryRepository, StageOneMemory
from corki.protocol.ids import ThreadId, new_thread_id
from corki.storage import SQLiteSessionRepository


def seed(
    connection,
    workspace,
    thread,
    source,
    *,
    used=None,
    count=0,
    selected=0,
    mode="enabled",
    raw="raw",
    summary="summary",
    generated=None,
):
    connection.execute(
        "INSERT INTO threads(id,cwd,updated_at,memory_mode,preview) VALUES (?,?,?,?,'evidence')",
        (thread, str(workspace), source, mode),
    )
    connection.execute(
        "INSERT INTO memory_stage1_outputs(thread_id,cwd,source_updated_at,raw_memory,"
        "rollout_summary,usage_count,last_used_at,selected_for_phase2,generated_at) "
        "VALUES (?,?,?,?,?,?,?,?,COALESCE(?,CURRENT_TIMESTAMP))",
        (thread, str(workspace), source, raw, summary, count, used, selected, generated),
    )
    connection.execute(
        "INSERT INTO memory_jobs(kind,job_key,status,source_updated_at,"
        "last_success_source_updated_at) "
        "VALUES ('memory_stage1',?,'succeeded',?,?)",
        (thread, source, source),
    )


@pytest.fixture
def store(tmp_path, monkeypatch):
    database = tmp_path / "sessions.db"
    SQLiteSessionRepository(database)
    repository = SQLiteMemoryRepository(database)
    now = datetime(2030, 3, 1, 12, tzinfo=UTC)
    monkeypatch.setattr("corki.memory.sqlite.time.time", lambda: now.timestamp())
    return database, repository, now


def test_selection_uses_source_not_generation_and_last_use_overrides_source(tmp_path, store):
    database, repository, now = store
    recent = (now - timedelta(days=1)).isoformat()
    old = (now - timedelta(days=40)).isoformat()
    with sqlite3.connect(database) as connection:
        seed(connection, tmp_path, "old-never-used", old, generated=recent)
        seed(connection, tmp_path, "fresh-unused", recent, generated=old)
        seed(connection, tmp_path, "stale-used", recent, used=old, count=99, generated=recent)
        seed(connection, tmp_path, "recent-used", old, used=recent, count=1, generated=old)

    async def scenario():
        selected = await repository.load_consolidation_inputs(limit=10, max_unused_days=30)
        assert tuple(row.thread_id for row in selected) == ("fresh-unused", "recent-used")
        top = await repository.load_consolidation_inputs(limit=1, max_unused_days=30)
        assert tuple(row.thread_id for row in top) == ("recent-used",)

    asyncio.run(scenario())


def test_selection_excludes_disabled_polluted_empty_before_limit(tmp_path, store):
    database, repository, now = store
    with sqlite3.connect(database) as connection:
        for index, mode in enumerate(("disabled", "polluted")):
            seed(connection, tmp_path, f"ineligible-{index}", now.isoformat(), mode=mode, count=100)
        seed(connection, tmp_path, "empty", now.isoformat(), raw="  ", summary=" ", count=100)
        seed(connection, tmp_path, "a-valid", now.isoformat(), raw="", summary="useful summary")
        seed(connection, tmp_path, "z-valid", now.isoformat(), raw="useful raw", summary="")

    async def scenario():
        selected = await repository.load_consolidation_inputs(limit=2, max_unused_days=30)
        assert tuple(row.thread_id for row in selected) == ("a-valid", "z-valid")
        assert await repository.load_consolidation_inputs(limit=0, max_unused_days=30) == ()

    asyncio.run(scenario())


def test_top_n_ranking_and_stable_return_order_are_separate(tmp_path, store):
    database, repository, now = store
    with sqlite3.connect(database) as connection:
        for name in ("a", "b", "c"):
            seed(connection, tmp_path, name, now.isoformat(), generated=now.isoformat())

    async def scenario():
        selected = await repository.load_consolidation_inputs(limit=2, max_unused_days=30)
        assert tuple(row.thread_id for row in selected) == ("b", "c")
        await repository.mark_memories_used((ThreadId("c"), ThreadId("c")))
        # A rank change inside the same chosen set must not reorder its serialized input.
        selected = await repository.load_consolidation_inputs(limit=2, max_unused_days=30)
        assert tuple(row.thread_id for row in selected) == ("b", "c")

    asyncio.run(scenario())


def test_top_n_ranks_source_versions_at_microsecond_precision(tmp_path, store):
    database, repository, now = store
    older = now.replace(microsecond=100000).isoformat()
    newer = now.replace(microsecond=100001).isoformat()
    with sqlite3.connect(database) as connection:
        # The thread-id tiebreaker favors z-old if the timestamps collapse.
        seed(connection, tmp_path, "z-old", older)
        seed(connection, tmp_path, "a-new", newer)

    selected = asyncio.run(repository.load_consolidation_inputs(limit=1, max_unused_days=30))
    assert tuple(row.thread_id for row in selected) == ("a-new",)


def test_prune_is_bounded_preserves_selected_and_success_watermarks(tmp_path, store):
    database, repository, now = store
    cutoff = now - timedelta(days=30)
    with sqlite3.connect(database) as connection:
        for name in ("a-old", "b-old", "c-old"):
            seed(connection, tmp_path, name, (cutoff - timedelta(days=10)).isoformat())
        seed(connection, tmp_path, "selected", (cutoff - timedelta(days=5)).isoformat(), selected=1)
        seed(
            connection,
            tmp_path,
            "used",
            (cutoff - timedelta(days=4)).isoformat(),
            used=now.isoformat(),
        )
        seed(connection, tmp_path, "boundary", cutoff.isoformat())
        seed(
            connection,
            tmp_path,
            "stale-used",
            now.isoformat(),
            used=(cutoff - timedelta(seconds=1)).isoformat(),
        )
        jobs = connection.execute("SELECT * FROM memory_jobs ORDER BY job_key").fetchall()

    async def scenario():
        assert await repository.prune_stage_one_outputs(max_unused_days=30, limit=0) == 0
        assert await repository.prune_stage_one_outputs(max_unused_days=30, limit=2) == 2
        with sqlite3.connect(database) as connection:
            ids = connection.execute(
                "SELECT thread_id FROM memory_stage1_outputs ORDER BY thread_id"
            ).fetchall()
            assert ids == [
                (name,) for name in ("boundary", "c-old", "selected", "stale-used", "used")
            ]
        assert await repository.prune_stage_one_outputs(max_unused_days=30, limit=200) == 2
        with sqlite3.connect(database) as connection:
            assert (
                connection.execute("SELECT * FROM memory_jobs ORDER BY job_key").fetchall() == jobs
            )
            assert connection.execute(
                "SELECT thread_id FROM memory_stage1_outputs ORDER BY thread_id"
            ).fetchall() == [("boundary",), ("selected",), ("used",)]
        # Pruning output cannot make a previously successful source eligible again.
        assert not await repository.claim_extraction_jobs(
            current_thread_id=new_thread_id(),
            max_age_days=100,
            min_idle_hours=0,
            limit=10,
            lease_seconds=60,
        )

    asyncio.run(scenario())


def test_extraction_refresh_preserves_previous_selected_baseline_until_phase2_succeeds(
    tmp_path, store
):
    database, repository, now = store
    source = now - timedelta(days=50)
    with sqlite3.connect(database) as connection:
        seed(connection, tmp_path, "source", source.isoformat(), selected=1)
        connection.execute(
            "UPDATE memory_stage1_outputs SET selected_source_updated_at=source_updated_at"
        )
        connection.execute(
            "UPDATE threads SET updated_at=?", ((source + timedelta(days=1)).isoformat(),)
        )

    async def scenario():
        (claim,) = await repository.claim_extraction_jobs(
            current_thread_id=new_thread_id(),
            max_age_days=100,
            min_idle_hours=0,
            limit=1,
            lease_seconds=60,
        )
        replacement = StageOneMemory(
            claim.thread_id, tmp_path, claim.source_updated_at, "new raw", "new summary"
        )
        assert await repository.complete_extraction(claim, replacement)
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT selected_for_phase2,selected_source_updated_at FROM memory_stage1_outputs"
            ).fetchone() == (1, source.isoformat())
        assert await repository.prune_stage_one_outputs(max_unused_days=30, limit=200) == 0
        # Current inputs age out even though the previous successful baseline stays protected.
        assert await repository.load_consolidation_inputs(limit=10, max_unused_days=30) == ()
        phase2 = await repository.claim_consolidation(lease_seconds=60)
        assert phase2 is not None and await repository.complete_consolidation(phase2, ())
        assert await repository.prune_stage_one_outputs(max_unused_days=30, limit=200) == 1

    asyncio.run(scenario())


def test_usage_counts_each_signal_ignores_missing_and_uses_one_timestamp(tmp_path, store):
    database, repository, now = store
    with sqlite3.connect(database) as connection:
        for name in ("a", "b"):
            seed(connection, tmp_path, name, (now - timedelta(days=60)).isoformat())

    async def scenario():
        await repository.mark_memories_used(map(ThreadId, ("a", "a", "b", "missing")))
        selected = await repository.load_consolidation_inputs(limit=2, max_unused_days=30)
        assert [(row.thread_id, row.usage_count) for row in selected] == [("a", 2), ("b", 1)]
        assert len({row.last_used_at for row in selected}) == 1
        assert datetime.fromisoformat(selected[0].last_used_at.replace("Z", "+00:00")) == now
        assert await repository.prune_stage_one_outputs(max_unused_days=30, limit=200) == 0

    asyncio.run(scenario())
