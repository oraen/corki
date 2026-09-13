"""Owned stage-one completion mirrors source upsert and no-output contracts."""

import asyncio
import sqlite3
import threading
from datetime import UTC, datetime, timedelta, timezone

import pytest

from corki.memory import SQLiteMemoryRepository, StageOneMemory
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import UserMessageItem
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize("stored_age", [3, 2, 1, "micro_newer", "offset_equal", "legacy_older"])
@pytest.mark.parametrize("empty", [False, True])
def test_owned_completion_does_not_overwrite_newer_output_but_no_output_deletes(
    tmp_path, stored_age, empty
):
    async def scenario():
        database = tmp_path / "history.db"
        sessions = SQLiteSessionRepository(database)
        source = new_thread_id()
        await sessions.create_thread(source, tmp_path)
        await sessions.append_items(source, (UserMessageItem("evidence", new_turn_id()),))
        repository = SQLiteMemoryRepository(database)
        now = datetime.now(UTC)
        claimed = (now - timedelta(hours=2)).isoformat()
        instant = datetime.fromisoformat(claimed)
        if stored_age == "micro_newer":
            stored = (instant + timedelta(microseconds=1)).isoformat()
        elif stored_age == "offset_equal":
            stored = instant.astimezone(timezone(timedelta(hours=5, minutes=30))).isoformat()
        elif stored_age == "legacy_older":
            stored = (instant - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
        else:
            stored = (now - timedelta(hours=stored_age)).isoformat()
        keep = stored_age in {1, "micro_newer"}
        with repository._connect() as db:
            db.execute("UPDATE threads SET updated_at=? WHERE id=?", (claimed, source))
        claim = (
            await repository.claim_extraction_jobs(
                current_thread_id=new_thread_id(),
                max_age_days=30,
                min_idle_hours=0,
                limit=1,
                lease_seconds=3600,
            )
        )[0]
        with repository._connect() as db:
            db.execute(
                "INSERT INTO memory_stage1_outputs(thread_id, source_updated_at, cwd, raw_memory, "
                "rollout_summary, generated_at, usage_count, selected_for_phase2, "
                "selected_source_updated_at) "
                "VALUES (?, ?, ?, 'existing', 'existing summary', 'old', 11, 1, ?)",
                (source, stored, str(tmp_path), stored),
            )
        value = None if empty else StageOneMemory(source, tmp_path, claimed, "snapshot", "summary")
        assert await repository.complete_extraction(claim, value)
        with repository._connect() as db:
            row = db.execute(
                "SELECT source_updated_at, raw_memory, usage_count, selected_for_phase2, "
                "selected_source_updated_at FROM memory_stage1_outputs WHERE thread_id=?",
                (source,),
            ).fetchone()
            expected = (
                None
                if empty
                else (
                    stored if keep else claimed,
                    "existing" if keep else "snapshot",
                    11,
                    1,
                    stored,
                )
            )
            assert (tuple(row) if row is not None else None) == expected
            job = db.execute(
                "SELECT status, last_success_source_updated_at FROM memory_jobs WHERE job_key=?",
                (source,),
            ).fetchone()
            assert tuple(job) == ("succeeded", claimed)
            assert (
                db.execute("SELECT status FROM memory_jobs WHERE job_key='global'").fetchone()[0]
                == "pending"
            )
        assert not await repository.complete_extraction(claim, value)

    asyncio.run(scenario())


async def _claimed(tmp_path):
    database = tmp_path / "history.db"
    sessions = SQLiteSessionRepository(database)
    source = new_thread_id()
    await sessions.create_thread(source, tmp_path)
    await sessions.append_items(source, (UserMessageItem("evidence", new_turn_id()),))
    repository = SQLiteMemoryRepository(database)
    claim = (
        await repository.claim_extraction_jobs(
            current_thread_id=new_thread_id(),
            max_age_days=30,
            min_idle_hours=0,
            limit=1,
            lease_seconds=3600,
        )
    )[0]
    return repository, source, claim


@pytest.mark.parametrize("mode", ["enabled", "disabled", "polluted"])
def test_no_output_without_prior_row_advances_owned_success_without_enqueue(tmp_path, mode):
    async def scenario():
        repository, source, claim = await _claimed(tmp_path)
        await repository.mark_thread_mode(source, mode)
        assert await repository.complete_extraction(claim, None)
        with repository._connect() as db:
            assert db.execute("SELECT 1 FROM memory_jobs WHERE job_key='global'").fetchone() is None
            assert (
                db.execute(
                    "SELECT last_success_source_updated_at FROM memory_jobs WHERE job_key=?",
                    (source,),
                ).fetchone()[0]
                == claim.source_updated_at
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("empty", [False, True])
def test_artifact_transaction_failure_rolls_back_job_completion(tmp_path, empty):
    async def scenario():
        repository, source, claim = await _claimed(tmp_path)
        with repository._connect() as db:
            db.execute(
                "INSERT INTO memory_stage1_outputs(thread_id, source_updated_at, cwd, raw_memory, "
                "rollout_summary, generated_at) VALUES (?, ?, ?, 'old', 'old', 'old')",
                (source, claim.source_updated_at, str(tmp_path)),
            )
            operation = "DELETE" if empty else "INSERT"
            db.execute(
                f"CREATE TRIGGER reject_output BEFORE {operation} ON memory_stage1_outputs "
                "BEGIN SELECT RAISE(ABORT, 'output failure'); END"
            )
        value = (
            None
            if empty
            else StageOneMemory(source, tmp_path, claim.source_updated_at, "new", "new")
        )
        with pytest.raises(sqlite3.IntegrityError, match="output failure"):
            await repository.complete_extraction(claim, value)
        with repository._connect() as db:
            assert tuple(
                db.execute(
                    "SELECT status, ownership_token, last_success_source_updated_at "
                    "FROM memory_jobs WHERE job_key=?",
                    (source,),
                ).fetchone()
            ) == ("running", claim.ownership_token, None)
            assert (
                db.execute(
                    "SELECT raw_memory FROM memory_stage1_outputs WHERE thread_id=?", (source,)
                ).fetchone()[0]
                == "old"
            )
            assert db.execute("SELECT 1 FROM memory_jobs WHERE job_key='global'").fetchone() is None
            db.execute("DROP TRIGGER reject_output")
        assert await repository.complete_extraction(claim, value)

    asyncio.run(scenario())


def test_cancelled_completion_joins_started_database_worker(tmp_path, monkeypatch):
    async def scenario():
        repository, source, claim = await _claimed(tmp_path)
        entered, finished, release = asyncio.Event(), asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        original = repository._complete_extraction

        def held(*args):
            loop.call_soon_threadsafe(entered.set)
            try:
                assert release.wait(5)
                return original(*args)
            finally:
                loop.call_soon_threadsafe(finished.set)

        monkeypatch.setattr(repository, "_complete_extraction", held)
        value = StageOneMemory(source, tmp_path, claim.source_updated_at, "new", "new")
        task = asyncio.create_task(repository.complete_extraction(claim, value))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            done, _ = await asyncio.wait((task,), timeout=0.03)
            assert not done, "completion cancellation must retain ownership of started DB writes"
        finally:
            release.set()
            await asyncio.wait_for(finished.wait(), 1)
            with pytest.raises(asyncio.CancelledError):
                await task
        assert (await repository.load_consolidation_inputs(limit=10, max_unused_days=30))[
            0
        ].raw_memory == "new"

    asyncio.run(scenario())
