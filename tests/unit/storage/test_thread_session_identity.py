import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from corki.protocol.ids import SessionId, ThreadId, new_session_id, new_thread_id
from corki.protocol.memory import ThreadMemoryMode
from corki.storage import SQLiteSessionRepository, StorageIntegrityError


def test_legacy_wal_migration_is_serialized_and_preserves_thread_metadata(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        # Existing Corki databases use WAL before the new identity column exists.
        db.execute("PRAGMA journal_mode=WAL")
        db.execute(
            "CREATE TABLE threads(id TEXT PRIMARY KEY, cwd TEXT NOT NULL, "
            "created_at TEXT, updated_at TEXT)"
        )
        db.execute(
            "INSERT INTO threads VALUES ('legacy-root', ?, 'created', 'updated')", (str(tmp_path),)
        )
    with ThreadPoolExecutor(max_workers=4) as pool:
        repositories = list(pool.map(lambda _: SQLiteSessionRepository(path), range(4)))

    async def scenario():
        for repository in repositories:
            assert await repository.load_thread_session_id(ThreadId("legacy-root")) == "legacy-root"
        await repositories[0].create_thread(
            ThreadId("legacy-root"),
            tmp_path / "other",
            session_id=new_session_id(),
            memory_mode=ThreadMemoryMode.DISABLED,
        )
        with sqlite3.connect(path) as db:
            assert db.execute(
                "SELECT id,cwd,created_at,updated_at,memory_mode,session_id FROM threads"
            ).fetchone() == (
                "legacy-root",
                str(tmp_path),
                "created",
                "updated",
                "enabled",
                "legacy-root",
            )

    asyncio.run(scenario())


def test_concurrent_creation_keeps_one_complete_identity(tmp_path):
    path, thread = tmp_path / "race.db", new_thread_id()
    repositories = [SQLiteSessionRepository(path), SQLiteSessionRepository(path)]
    sessions = [new_session_id(), new_session_id()]

    async def scenario():
        await asyncio.gather(
            *(
                repository.create_thread(
                    thread,
                    tmp_path / str(index),
                    session_id=sessions[index],
                    memory_mode=ThreadMemoryMode.DISABLED if index else ThreadMemoryMode.ENABLED,
                )
                for index, repository in enumerate(repositories)
            )
        )
        winner = await repositories[0].load_thread_session_id(thread)
        index = sessions.index(winner)
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT cwd,memory_mode,session_id FROM threads").fetchone() == (
                str(tmp_path / str(index)),
                "disabled" if index else "enabled",
                winner,
            )
        await repositories[1].create_thread(thread, tmp_path, session_id=new_session_id())
        assert await repositories[1].load_thread_session_id(thread) == winner

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", ["", "bad\0id", None, b"bytes"])
def test_corrupt_identity_is_not_replaced(tmp_path, invalid):
    path, thread = tmp_path / "corrupt.db", new_thread_id()
    repository = SQLiteSessionRepository(path)

    async def scenario():
        await repository.create_thread(thread, tmp_path)
        with sqlite3.connect(path) as db:
            db.execute("UPDATE threads SET session_id=?", (invalid,))
        reopened = SQLiteSessionRepository(path)
        with pytest.raises(StorageIntegrityError, match="invalid stored"):
            await reopened.load_thread_session_id(thread)
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT session_id FROM threads").fetchone() == (invalid,)

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", ["", "bad\0id", 7, False])
def test_bad_initial_identity_fails_without_creating_thread(tmp_path, invalid):
    path = tmp_path / "bad.db"
    repository = SQLiteSessionRepository(path)

    async def scenario():
        with pytest.raises(ValueError, match="execution identity"):
            await repository.create_thread(new_thread_id(), tmp_path, session_id=invalid)
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT count(*) FROM threads").fetchone() == (0,)

    asyncio.run(scenario())


def test_missing_thread_identity_is_not_fabricated(tmp_path):
    repository = SQLiteSessionRepository(tmp_path / "missing.db")
    with pytest.raises(StorageIntegrityError, match="does not exist"):
        asyncio.run(repository.load_thread_session_id(ThreadId("missing")))


def test_default_new_root_identity_equals_thread_id(tmp_path):
    repository, thread = SQLiteSessionRepository(tmp_path / "root.db"), new_thread_id()

    async def scenario():
        await repository.create_thread(thread, tmp_path)
        assert await repository.load_thread_session_id(thread) == SessionId(str(thread))

    asyncio.run(scenario())
