"""Old-database migration and separate active/archive views preserve canonical data."""

import asyncio
import sqlite3
import threading

import pytest

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


@pytest.mark.parametrize("operation", ["read", "list", "archive", "unarchive", "missing"])
def test_archive_store_closes_connections_on_success_and_failure(tmp_path, monkeypatch, operation):
    async def scenario():
        database = tmp_path / "history.db"
        repository = SQLiteSessionRepository(database)
        thread = new_thread_id()
        try:
            await repository.create_thread(thread, tmp_path)
            await repository.append_items(thread, (UserMessageItem("history", new_turn_id()),))
        finally:
            await repository.close()
        store = SQLiteThreadArchiveStore(database)
        if operation == "unarchive":
            await store.archive(thread)
        connections = []

        def connect():
            connection = sqlite3.connect(database, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connections.append(connection)
            return connection

        monkeypatch.setattr(store, "_connect", connect)
        if operation == "missing":
            with pytest.raises(LookupError):
                await store.read(new_thread_id())
        elif operation == "list":
            await store.list_threads()
        else:
            await getattr(store, operation)(thread)
        assert connections
        for connection in connections:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")

    asyncio.run(scenario())


def test_cancelled_archive_metadata_read_joins_worker(tmp_path, monkeypatch):
    async def scenario():
        store = SQLiteThreadArchiveStore(tmp_path / "history.db")
        entered, released = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()

        def read(_):
            loop.call_soon_threadsafe(entered.set)
            assert released.wait(3)

        monkeypatch.setattr(store, "_read", read)
        task = asyncio.create_task(store.read(new_thread_id()))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            released.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            released.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_missing_session_database_is_not_recreated_by_read(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        asyncio.run(SQLiteThreadArchiveStore(path).read(new_thread_id()))
    assert not path.exists()
