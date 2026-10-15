"""The real reset transaction revokes claims while preserving non-memory state."""

import asyncio
import sqlite3

import pytest

from corki.memory import SQLiteMemoryRepository, StageOneMemory
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import UserMessageItem
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize("fail_transaction", [False, True])
@pytest.mark.parametrize("new_owner_state", ["running", "success", "empty", "failure"])
@pytest.mark.parametrize("late_outcome", ["success", "empty", "failure"])
def test_memory_reset_transaction_rows_modes_and_running_extraction(
    tmp_path, fail_transaction, new_owner_state, late_outcome
):
    async def scenario():
        database = tmp_path / "history.db"
        sessions = SQLiteSessionRepository(database)
        thread, disabled = new_thread_id(), new_thread_id()
        await sessions.create_thread(thread, tmp_path)
        await sessions.create_thread(disabled, tmp_path)
        await sessions.append_items(
            thread, (UserMessageItem("retained conversation", new_turn_id()),)
        )
        repository = SQLiteMemoryRepository(database)
        await repository.mark_thread_mode(disabled, "disabled")
        with sqlite3.connect(database) as db:
            db.execute(
                "INSERT INTO memory_stage1_outputs(thread_id, source_updated_at, cwd, raw_memory, "
                "rollout_summary, generated_at) "
                "VALUES (?, 'old-version', ?, 'raw', 'summary', 'old')",
                (str(thread), str(tmp_path)),
            )
            db.execute(
                "INSERT INTO memory_jobs(kind, job_key, status) "
                "VALUES ('unrelated', 'keep', 'pending')"
            )
        claims = await repository.claim_extraction_jobs(
            current_thread_id=new_thread_id(),
            max_age_days=30,
            min_idle_hours=0,
            limit=5,
            lease_seconds=3600,
        )
        assert len(claims) == 1
        claim = claims[0]
        if fail_transaction:
            with sqlite3.connect(database) as db:
                db.execute(
                    "CREATE TRIGGER fail_reset BEFORE DELETE ON memory_jobs "
                    "BEGIN SELECT RAISE(ABORT, 'blocked reset'); END"
                )
            with pytest.raises(sqlite3.IntegrityError, match="blocked reset"):
                await repository.clear_memory_data()
            with sqlite3.connect(database) as db:
                assert db.execute("SELECT count(*) FROM memory_stage1_outputs").fetchone() == (1,)
                assert db.execute("SELECT count(*) FROM memory_jobs").fetchone() == (2,)
                db.execute("DROP TRIGGER fail_reset")
        await repository.clear_memory_data()
        assert not await repository.complete_extraction(
            claim, StageOneMemory(thread, tmp_path, claim.source_updated_at, "late", "late")
        )
        assert not await repository.fail_extraction(claim, "late failure", retry_delay_seconds=1)
        assert (await sessions.load_items(thread))[0].content == "retained conversation"
        with sqlite3.connect(database) as db:
            assert db.execute("SELECT count(*) FROM memory_stage1_outputs").fetchone() == (0,)
            assert db.execute("SELECT kind, job_key, status FROM memory_jobs").fetchall() == [
                ("unrelated", "keep", "pending")
            ]
            assert dict(db.execute("SELECT id, memory_mode FROM threads")) == {
                str(thread): "enabled",
                str(disabled): "disabled",
            }
        new_claims = await repository.claim_extraction_jobs(
            current_thread_id=new_thread_id(),
            max_age_days=30,
            min_idle_hours=0,
            limit=5,
            lease_seconds=3600,
        )
        assert len(new_claims) == 1 and new_claims[0].ownership_token != claim.ownership_token
        current = new_claims[0]
        reopened = SQLiteMemoryRepository(database)
        fresh = StageOneMemory(thread, tmp_path, current.source_updated_at, "fresh", "fresh")
        if new_owner_state == "success":
            assert await reopened.complete_extraction(current, fresh)
        elif new_owner_state == "empty":
            assert await reopened.complete_extraction(current, None)
        elif new_owner_state == "failure":
            assert await reopened.fail_extraction(current, "fresh failure", retry_delay_seconds=60)

        def snapshot():
            with sqlite3.connect(database) as db:
                return (
                    db.execute("SELECT * FROM memory_jobs ORDER BY kind, job_key").fetchall(),
                    db.execute("SELECT * FROM memory_stage1_outputs ORDER BY thread_id").fetchall(),
                )

        before = snapshot()
        if late_outcome == "failure":
            assert not await repository.fail_extraction(claim, "stale", retry_delay_seconds=99)
        else:
            stale = StageOneMemory(thread, tmp_path, claim.source_updated_at, "stale", "stale")
            assert not await repository.complete_extraction(
                claim, stale if late_outcome == "success" else None
            )
        assert snapshot() == before
        if new_owner_state == "running":
            assert await reopened.complete_extraction(current, fresh)
        with sqlite3.connect(database) as db:
            assert db.execute(
                "SELECT raw_memory, rollout_summary FROM memory_stage1_outputs"
            ).fetchall() == (
                [("fresh", "fresh")] if new_owner_state in {"running", "success"} else []
            )
        await reopened.close()
        await repository.close()
        await sessions.close()

    asyncio.run(scenario())
