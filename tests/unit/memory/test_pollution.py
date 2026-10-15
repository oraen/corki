import asyncio
import sqlite3
import threading
import time

import pytest

from corki.memory import SQLiteMemoryRepository
from corki.protocol.ids import ThreadId
from corki.storage import SQLiteSessionRepository


@pytest.fixture
def store(tmp_path):
    database = tmp_path / "sessions.db"
    SQLiteSessionRepository(database)
    repository = SQLiteMemoryRepository(database)
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO threads(id,cwd) VALUES ('source',?)", (str(tmp_path),))
        connection.execute(
            "INSERT INTO memory_stage1_outputs(thread_id,cwd,source_updated_at,"
            "raw_memory,rollout_summary,selected_for_phase2) "
            "VALUES ('source',?,CURRENT_TIMESTAMP,'raw','summary',1)",
            (str(tmp_path),),
        )
    return database, repository


@pytest.mark.parametrize("initial_mode", ["enabled", "polluted"])
@pytest.mark.parametrize("job_state", [None, "running", "succeeded"])
def test_pollution_enqueues_selected_sources_without_breaking_owner_or_cooldown(
    store, monkeypatch, initial_mode, job_state
):
    database, repository = store
    now = int(time.time())
    monkeypatch.setattr("corki.memory.sqlite.time.time_ns", lambda: 10)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE threads SET memory_mode=?", (initial_mode,))
        if job_state:
            connection.execute(
                "INSERT INTO memory_jobs(kind,job_key,status,ownership_token,lease_until,"
                "input_watermark,completed_watermark,finished_at) "
                "VALUES ('memory_consolidate_global','global',?,'owner',?,17,13,?)",
                (job_state, now + 60, now if job_state == "succeeded" else None),
            )

    async def scenario():
        await repository.mark_thread_mode(ThreadId("source"), "polluted")
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT status,input_watermark,completed_watermark,ownership_token,"
                "lease_until,finished_at "
                "FROM memory_jobs WHERE job_key='global'"
            ).fetchone()
        assert row is not None and row[1] == (18 if job_state else 10)
        assert row[0] == ("running" if job_state == "running" else "pending")
        if job_state == "running":
            assert row[3:5] == ("owner", now + 60)
        if job_state:
            assert row[2] == 13
            assert await repository.claim_consolidation(lease_seconds=60) is None
        await repository.mark_thread_mode(ThreadId("source"), "polluted")
        with sqlite3.connect(database) as connection:
            assert (
                connection.execute(
                    "SELECT input_watermark FROM memory_jobs WHERE job_key='global'"
                ).fetchone()[0]
                == row[1] + 1
            )
            assert (
                connection.execute(
                    "SELECT selected_for_phase2 FROM memory_stage1_outputs"
                ).fetchone()[0]
                == 1
            )
        assert await repository.load_consolidation_inputs(limit=10, max_unused_days=30) == ()

    asyncio.run(scenario())


@pytest.mark.parametrize("missing", [False, True])
def test_unselected_or_missing_thread_does_not_enqueue(store, missing):
    database, repository = store
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE memory_stage1_outputs SET selected_for_phase2=0")
    asyncio.run(
        repository.mark_thread_mode(ThreadId("missing" if missing else "source"), "polluted")
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM memory_jobs").fetchone()[0] == 0


def test_pollution_and_enqueue_roll_back_together(store, monkeypatch):
    database, repository = store

    def fail(connection, watermark):
        raise sqlite3.OperationalError("injected enqueue failure")

    monkeypatch.setattr("corki.memory.sqlite.enqueue_consolidation", fail)
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        asyncio.run(repository.mark_thread_mode(ThreadId("source"), "polluted"))
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT memory_mode FROM threads").fetchone()[0] == "enabled"


def test_pollution_cancellation_joins_started_write(store, monkeypatch):
    database, repository = store
    started, release = threading.Event(), threading.Event()
    actual = repository._mark_thread_mode

    def blocked(*args):
        started.set()
        assert release.wait(3)
        actual(*args)

    monkeypatch.setattr(repository, "_mark_thread_mode", blocked)

    async def scenario():
        task = asyncio.create_task(repository.mark_thread_mode(ThreadId("source"), "polluted"))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            await asyncio.sleep(0.01)
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT memory_mode FROM threads").fetchone()[0] == "polluted"
            assert (
                connection.execute(
                    "SELECT status FROM memory_jobs WHERE job_key='global'"
                ).fetchone()[0]
                == "pending"
            )

    asyncio.run(scenario())
