"""Historical source identity survives migration and a different resuming host."""

import asyncio
import sqlite3

import pytest

from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import UserMessageItem
from corki.protocol.session_source import SessionSource
from corki.storage import SQLiteSessionRepository


def test_concurrent_legacy_source_migration_preserves_original_rows(tmp_path):
    async def scenario():
        path = tmp_path / "history.db"
        repository = SQLiteSessionRepository(path)
        thread = new_thread_id()
        await repository.create_thread(thread, tmp_path)
        await repository.append_items(thread, (UserMessageItem("request", new_turn_id()),))
        with sqlite3.connect(path) as db:
            db.execute("ALTER TABLE threads DROP COLUMN source")
            before = db.execute("SELECT * FROM threads").fetchall()
            history = db.execute("SELECT * FROM conversation_items").fetchall()
        reopened = await asyncio.gather(
            *(asyncio.to_thread(SQLiteSessionRepository, path) for _ in range(4))
        )
        assert await reopened[0].load_thread_source(thread) == SessionSource()
        with sqlite3.connect(path) as db:
            assert [row[:-1] for row in db.execute("SELECT * FROM threads")] == before
            assert db.execute("SELECT * FROM conversation_items").fetchall() == history
        for owner in reopened:
            await owner.close()
        await repository.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("stored", ["cli", "vscode", "atlas", "chatgpt"])
def test_source_filter_precedes_scan_limit_and_retains_legacy_bare_values(
    tmp_path, monkeypatch, stored
):
    from corki.memory import SQLiteMemoryRepository

    async def scenario():
        path = tmp_path / "history.db"
        sessions = SQLiteSessionRepository(path)
        eligible, excluded = new_thread_id(), new_thread_id()
        await sessions.create_thread(eligible, tmp_path)
        await sessions.create_thread(
            excluded, tmp_path, session_source=SessionSource.from_startup_arg("exec")
        )
        for thread in (eligible, excluded):
            await sessions.append_items(thread, (UserMessageItem("request", new_turn_id()),))
        with sqlite3.connect(path) as db:
            db.execute(
                "UPDATE threads SET source=?, updated_at=datetime('now','-1 day') WHERE id=?",
                (stored, eligible),
            )
        monkeypatch.setattr("corki.memory.extraction.THREAD_SCAN_LIMIT", 1)
        memories = SQLiteMemoryRepository(path)
        claims = await memories.claim_extraction_jobs(
            current_thread_id=new_thread_id(),
            max_age_days=30,
            min_idle_hours=0,
            limit=1,
            lease_seconds=60,
        )
        assert [claim.thread_id for claim in claims] == [eligible]
        assert await memories.complete_extraction(claims[0], None)
        await memories.close()
        await sessions.close()

    asyncio.run(scenario())


def test_different_resuming_host_cannot_overwrite_historical_source(tmp_path):
    async def scenario():
        path = tmp_path / "history.db"
        repository = SQLiteSessionRepository(path)
        thread = new_thread_id()
        original = SessionSource.from_startup_arg("exec")
        await repository.create_thread(thread, tmp_path, session_source=original)
        reopened = SQLiteSessionRepository(path)
        await reopened.create_thread(
            thread, tmp_path, session_source=SessionSource.internal("guardian")
        )
        assert await reopened.load_thread_source(thread) == original
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT source FROM threads").fetchone() == ("exec",)
        await reopened.close()
        await repository.close()

    asyncio.run(scenario())
