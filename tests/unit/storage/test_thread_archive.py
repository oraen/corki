"""Old-database migration and separate active/archive views preserve canonical data."""

import asyncio
import sqlite3

from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import UserMessageItem
from corki.storage import SQLiteSessionRepository
from corki.storage.thread_archive import SQLiteThreadArchiveStore


def test_archive_migration_and_reopening_preserve_history_and_collection(tmp_path):
    async def scenario():
        database = tmp_path / "history.db"
        sessions = SQLiteSessionRepository(database)
        thread = new_thread_id()
        await sessions.create_thread(thread, tmp_path)
        history = (UserMessageItem("old history", new_turn_id()),)
        await sessions.append_items(thread, history)
        with sqlite3.connect(database) as db:
            db.execute("ALTER TABLE threads DROP COLUMN archived_at")
        migrated = SQLiteSessionRepository(database)
        store = SQLiteThreadArchiveStore(database)
        original = await store.read(thread)
        assert original.archived_at is None and original.has_history
        assert await migrated.load_items(thread) == history
        archived = await store.archive(thread)
        reopened = SQLiteSessionRepository(database)
        assert await store.read(thread) == archived
        assert await reopened.load_items(thread) == history
        assert await store.list_threads() == ()
        assert await store.list_threads(archived=True, cwd=tmp_path) == (archived,)
        assert await store.list_threads(archived=True, limit=0) == ()
        restored = await store.unarchive(thread)
        assert restored.archived_at is None
        assert await store.list_threads() == (restored,)
        assert await store.list_threads(archived=True) == ()

    asyncio.run(scenario())
