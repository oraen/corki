import asyncio
import json
import os
import sys
import threading
from dataclasses import replace

import pytest

from corki.protocol.ids import SessionId, ThreadId, new_turn_id
from corki.protocol.items import UserMessageItem
from corki.storage.sqlite import SQLiteSessionRepository, StorageIntegrityError
from corki.storage.volatile import VolatileSessionRepository


def test_only_pending_transcripts_are_refreshed(tmp_path, monkeypatch):
    from corki.storage import transcripts

    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "history.db")
        threads = (ThreadId("first"), ThreadId("second"))
        for thread in threads:
            await repository.create_thread(thread, tmp_path)
            await repository.materialize_transcript(thread)
        calls = []
        flush = transcripts.flush_transcript

        def observed(repository, thread):
            calls.append(str(thread))
            return flush(repository, thread)

        monkeypatch.setattr(transcripts, "flush_transcript", observed)
        item = UserMessageItem("new record", new_turn_id())
        await repository.append_items(threads[0], (item,))
        assert calls == ["first"]
        calls.clear()
        await repository.append_items(threads[0], (item,))
        await repository.create_thread(ThreadId("unregistered"), tmp_path)
        assert calls == []
        with pytest.raises(StorageIntegrityError, match="collision"):
            await repository.append_items(
                threads[0],
                (UserMessageItem("rolled back", new_turn_id()), replace(item, content="collision")),
            )
        assert await repository.load_items(threads[0]) == (item,)
        with repository._connect() as connection:
            assert connection.execute("SELECT * FROM transcript_pending").fetchall() == []
        assert calls == []
        await repository.close()

    asyncio.run(scenario())


def test_concurrent_repository_writers_publish_one_canonical_sequence(tmp_path):
    async def scenario():
        database = tmp_path / "history.db"
        repositories = [SQLiteSessionRepository(database) for _ in range(4)]
        thread = ThreadId("shared")
        await repositories[0].create_thread(thread, tmp_path)
        path = await repositories[0].materialize_transcript(thread)
        inode = path.stat().st_ino
        items = tuple(UserMessageItem(f"item-{i}", new_turn_id()) for i in range(32))
        try:
            with path.open() as reader:
                metadata = json.loads(reader.readline())
                await asyncio.gather(
                    *(
                        repositories[i % 4].append_items(thread, (item,))
                        for i, item in enumerate(items)
                    )
                )
                stored = await repositories[0].load_items(thread)
                rows = [json.loads(line) for line in reader]
                assert metadata["thread_id"] == thread
                assert path.stat().st_ino == inode
                assert [row["sequence"] for row in rows] == list(range(32))
                assert [row["payload"]["id"] for row in rows] == [str(item.id) for item in stored]
                assert {row["payload"]["content"] for row in rows} == {
                    item.content for item in items
                }
                await asyncio.gather(
                    *(repository.append_items(thread, items) for repository in repositories)
                )
                assert reader.read() == ""
                with repositories[0]._connect() as connection:
                    assert connection.execute("SELECT * FROM transcript_pending").fetchall() == []
        finally:
            for repository in repositories:
                await repository.close()

    asyncio.run(scenario())


def test_independent_processes_share_transcript_publication_lock(tmp_path):
    async def scenario():
        database = tmp_path / "history.db"
        repository = SQLiteSessionRepository(database)
        thread = ThreadId("shared")
        await repository.create_thread(thread, tmp_path)
        path = await repository.materialize_transcript(thread)
        script = """
import asyncio, sys
from pathlib import Path
from corki.storage.sqlite import SQLiteSessionRepository
from corki.protocol.ids import ThreadId, new_turn_id
from corki.protocol.items import UserMessageItem
async def main():
    repository = SQLiteSessionRepository(Path(sys.argv[1]))
    try:
        for i in range(8):
            await repository.append_items(
                ThreadId('shared'), (UserMessageItem(f'{sys.argv[2]}-{i}', new_turn_id()),)
            )
    finally:
        await repository.close()
asyncio.run(main())
"""
        processes = []
        try:
            with path.open() as reader:
                reader.readline()
                for label in ("first", "second"):
                    processes.append(
                        await asyncio.create_subprocess_exec(
                            sys.executable,
                            "-c",
                            script,
                            str(database),
                            label,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                    )
                async with asyncio.timeout(15):
                    outputs = await asyncio.gather(*(p.communicate() for p in processes))
                for process, (_, stderr) in zip(processes, outputs, strict=True):
                    assert process.returncode == 0, stderr.decode()
                rows = [json.loads(line) for line in reader]
                stored = await repository.load_items(thread)
                assert [row["sequence"] for row in rows] == list(range(16))
                assert [row["payload"]["id"] for row in rows] == [str(item.id) for item in stored]
                assert {row["payload"]["content"] for row in rows} == {
                    f"{label}-{i}" for label in ("first", "second") for i in range(8)
                }
        finally:
            for process in processes:
                if process.returncode is None:
                    process.kill()
                await process.wait()
            await repository.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("legacy", [False, True])
def test_pending_publication_survives_cold_open_and_legacy_upgrade(tmp_path, monkeypatch, legacy):
    async def scenario():
        database = tmp_path / "history.db"
        repository = SQLiteSessionRepository(database)
        thread = ThreadId("first")
        await repository.create_thread(thread, tmp_path)
        path = await repository.materialize_transcript(thread)
        before = path.read_bytes()
        item = UserMessageItem("not yet published", new_turn_id())

        def fail_publication(*args):
            raise OSError("injected publication failure")

        with monkeypatch.context() as patch:
            patch.setattr("corki.storage.transcripts.flush_transcript", fail_publication)
            await repository.append_items(thread, (item,))
        assert path.read_bytes() == before
        with repository._connect() as connection:
            assert (
                connection.execute("SELECT thread_id FROM transcript_pending").fetchone()[0]
                == thread
            )
            if legacy:
                # Recreate the pre-pending-table schema in this isolated fixture.
                connection.execute("DROP TRIGGER transcript_pending_after_item")
                connection.execute("DROP TABLE transcript_pending")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE name='transcript_pending_v1'"
                )
        await repository.close()
        cold = SQLiteSessionRepository(database)
        # An unrelated business commit also retries durable outstanding work.
        await cold.create_thread(ThreadId("other"), tmp_path)
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert [row["payload"]["content"] for row in rows[1:]] == [item.content]
        with cold._connect() as connection:
            assert connection.execute("SELECT * FROM transcript_pending").fetchall() == []
        await cold.close()
        reopened = SQLiteSessionRepository(database)
        with reopened._connect() as connection:
            assert connection.execute("SELECT * FROM transcript_pending").fetchall() == []
        await reopened.close()

    asyncio.run(scenario())


def test_cancellation_joins_post_commit_publication(tmp_path, monkeypatch):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "history.db")
        thread = ThreadId("thread")
        await repository.create_thread(thread, tmp_path)
        path = await repository.materialize_transcript(thread)
        entered = threading.Event()
        release = threading.Event()
        publish = repository._after_durable_write

        def held_publish():
            entered.set()
            assert release.wait(5), "test did not release publisher"
            publish()

        monkeypatch.setattr(repository, "_after_durable_write", held_publish)
        item = UserMessageItem("committed before cancellation", new_turn_id())
        writer = asyncio.create_task(repository.append_items(thread, (item,)))
        try:
            async with asyncio.timeout(5):
                while not entered.is_set():
                    await asyncio.sleep(0.001)
            assert await repository.load_items(thread) == (item,)
            writer.cancel()
            await asyncio.sleep(0)
            writer.cancel()
            await asyncio.sleep(0)
            assert not writer.done()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await writer
            await repository.close()
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert [row["payload"]["content"] for row in rows[1:]] == [item.content]

    asyncio.run(scenario())


def test_fsync_failure_does_not_roll_back_commit_or_duplicate_retry(tmp_path, monkeypatch, caplog):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "history.db")
        thread = ThreadId("thread")
        await repository.create_thread(thread, tmp_path)
        path = await repository.materialize_transcript(thread)
        item = UserMessageItem("durable business history", new_turn_id())

        def fail_sync(descriptor):
            raise OSError("injected transcript fsync failure")

        with monkeypatch.context() as patch:
            patch.setattr("corki.storage.transcripts.os.fsync", fail_sync)
            await repository.append_items(thread, (item,))
        assert await repository.load_items(thread) == (item,)
        assert "publication failed" in caplog.text
        assert await repository.materialize_transcript(thread) == path
        await repository.append_items(thread, (item,))
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert [row["payload"]["content"] for row in rows[1:]] == [item.content]
        await repository.close()

    asyncio.run(scenario())


def test_materialized_transcript_follows_commits_and_cold_repository(tmp_path):
    async def scenario():
        database = tmp_path / "history.db"
        repository = SQLiteSessionRepository(database)
        thread = ThreadId("../opaque-thread")
        await repository.create_thread(thread, tmp_path, session_id=SessionId("different-session"))
        assert not database.with_name("history.db.transcripts").exists()
        first = UserMessageItem("first", new_turn_id())
        await repository.append_items(thread, (first,))
        path = await repository.materialize_transcript(thread)
        assert path.parent == tmp_path / "history.db.transcripts"
        assert path.stat().st_mode & 0o777 == 0o600
        with path.open() as reader:
            initial = [json.loads(line) for line in reader]
            assert initial[0]["session_id"] == "different-session"
            assert initial[1]["payload"]["content"] == "first"
            await repository.append_items(thread, (first,))
            assert reader.read() == ""
            await repository.close()
            cold = SQLiteSessionRepository(database)
            second = UserMessageItem("second", new_turn_id())
            await cold.append_items(thread, (second,))
            added = [json.loads(line) for line in reader]
            assert [row["payload"]["content"] for row in added] == ["second"]
            assert added[0]["sequence"] == 1
            assert await cold.materialize_transcript(thread) == path
            assert reader.read() == ""
            await cold.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["partial", "foreign", "symlink", "hardlink"])
def test_projection_recovery_never_overwrites_foreign_content(tmp_path, failure, caplog):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "history.db")
        thread = ThreadId("thread")
        await repository.create_thread(thread, tmp_path)
        path = await repository.materialize_transcript(thread)
        prefix = path.read_bytes()
        first = UserMessageItem("first", new_turn_id())
        await repository.append_items(thread, (first,))
        complete = path.read_bytes()
        if failure == "partial":
            path.write_bytes(complete[:-5])
        elif failure == "foreign":
            path.write_bytes(b"DO NOT OVERWRITE\n")
        else:
            path.unlink()
            target = tmp_path / "foreign"
            target.write_bytes(prefix)
            target.chmod(0o600)
            if failure == "symlink":
                path.symlink_to(target)
            else:
                os.link(target, path)
        second = UserMessageItem("second", new_turn_id())
        await repository.append_items(thread, (second,))
        assert await repository.load_items(thread) == (first, second)
        if failure == "partial":
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            assert [row["payload"]["content"] for row in rows[1:]] == ["first", "second"]
        else:
            assert "publication failed" in caplog.text
            assert path.read_bytes() == (b"DO NOT OVERWRITE\n" if failure == "foreign" else prefix)
            with pytest.raises(OSError):
                await repository.materialize_transcript(thread)
        await repository.close()

    asyncio.run(scenario())


def test_ephemeral_and_missing_threads_do_not_materialize(tmp_path):
    async def scenario():
        ephemeral = VolatileSessionRepository()
        thread = ThreadId("thread")
        await ephemeral.create_thread(thread, tmp_path)
        await ephemeral.append_items(thread, (UserMessageItem("private", new_turn_id()),))
        assert await ephemeral.materialize_transcript(thread) is None
        await ephemeral.close()
        assert list(tmp_path.iterdir()) == []
        persistent = SQLiteSessionRepository(tmp_path / "history.db")
        assert await persistent.materialize_transcript(thread) is None
        assert not (tmp_path / "history.db.transcripts").exists()
        await persistent.close()

    asyncio.run(scenario())
