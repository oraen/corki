"""Deletion tests use disposable stores and never inspect personal sessions."""

import asyncio
import sqlite3
import sys
import uuid
from contextlib import closing
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
from service import Service
from test_bridge import FakeRuntime, setup

from corki.protocol.ids import ThreadId
from corki.storage.thread_writer import ThreadWriterConflict, ThreadWriterLease
from corki.storage.transcripts import transcript_name


def test_archive_then_physical_delete(tmp_path):
    async def check():
        setup(tmp_path)

        async def send(event):
            pass

        async def factory(**kw):
            return FakeRuntime(**kw)

        service = Service(tmp_path, send, factory)
        try:
            requests = []
            for content in ("delete this", "keep this"):
                request = dict(
                    requestId=str(uuid.uuid4()),
                    threadId=str(uuid.uuid4()),
                    content=content,
                    provider="test",
                    model="test-model",
                )
                requests.append(request)
                await service.submit(request)
                await asyncio.gather(*service.tasks.values())
            thread, other = (request["threadId"] for request in requests)
            await service.rename(dict(threadId=thread, title="custom title"))
            service.state.put("draft:" + thread, {"content": "draft"})
            service.state.put("ui", {"selected": thread, "collapsed": True})
            project = tmp_path / "main/default/keep.txt"
            project.write_text("workspace untouched")
            transcript_root = service.database.with_name("corki.db.transcripts")
            transcript_root.mkdir(exist_ok=True)
            transcript = transcript_root / transcript_name(thread)
            transcript.write_text("transcript body")
            with closing(sqlite3.connect(service.database)) as db:
                turn = db.execute("SELECT id FROM turns WHERE thread_id=?", (thread,)).fetchone()[0]
            for file, tables in (
                ("checkpoints.db", ("checkpoints", "writes")),
                ("goals_1.sqlite", ("thread_goals", "goal_usage", "goal_deferrals")),
                ("corki.history-notes.db", ("notes",)),
            ):
                with closing(sqlite3.connect(service.database.with_name(file))) as db, db:
                    for table in tables:
                        db.execute(f"CREATE TABLE {table} (thread_id TEXT, content TEXT)")
                        key = thread + ":" + turn if file == "checkpoints.db" else thread
                        db.executemany(
                            f"INSERT INTO {table} VALUES (?,?)", [(key, "delete"), (other, "keep")]
                        )
            assert await service.archive_thread(dict(threadId=thread)) == {"archived": True}
            assert await service.repository.thread_exists(ThreadId(thread))
            assert transcript.exists()
            assert len((await service.list_threads({}))["threads"]) == 1
            assert await service.delete(dict(threadId=thread)) == {"deleted": True}
            assert not await service.repository.thread_exists(ThreadId(thread))
            assert await service.repository.thread_exists(ThreadId(other))
            assert not transcript.exists()
            assert project.read_text() == "workspace untouched"
            assert not service.state.request(requests[0]["requestId"])
            assert service.state.request(requests[1]["requestId"])
            assert thread not in service.state.titles()
            assert service.state.get("draft:" + thread) is None
            assert service.state.get("ui") == {"selected": "new", "collapsed": True}
            with closing(sqlite3.connect(service.database)) as db:
                for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                    columns = [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')]
                    if "thread_id" in columns:
                        assert (
                            db.execute(
                                f'SELECT count(*) FROM "{table}" WHERE thread_id=?', (thread,)
                            ).fetchone()[0]
                            == 0
                        )
                assert not db.execute("PRAGMA foreign_key_check").fetchall()
            for file in ("checkpoints.db", "goals_1.sqlite", "corki.history-notes.db"):
                with closing(sqlite3.connect(service.database.with_name(file))) as db:
                    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                        assert db.execute(f"SELECT * FROM {table}").fetchall() == [(other, "keep")]
            # Retry after a lost success is harmless; old submits cannot resurrect it.
            await service.delete(dict(threadId=thread))
            with pytest.raises(ValueError, match="已删除"):
                await service.submit(requests[0])
            with pytest.raises(ValueError, match="已删除"):
                await service.write_state({"key": "draft:" + thread, "value": "stale"})
        finally:
            await service.close()

    asyncio.run(check())


def test_live_writer_and_transcript_failure_protect_history(tmp_path):
    async def check():
        async def send(event):
            pass

        service = Service(tmp_path, send)
        thread = ThreadId(str(uuid.uuid4()))
        await service.repository.create_thread(thread, tmp_path)
        lease = ThreadWriterLease(service.database, thread)
        await lease.acquire()
        try:
            with pytest.raises(ThreadWriterConflict):
                await service.delete({"threadId": str(thread)})
            assert await service.repository.thread_exists(thread)
        finally:
            await lease.aclose()
        target = tmp_path / "external"
        target.mkdir()
        file = target / transcript_name(thread)
        file.write_text("untouched")
        service.database.with_name("corki.db.transcripts").symlink_to(
            target, target_is_directory=True
        )
        with pytest.raises(OSError):
            await service.delete({"threadId": str(thread)})
        assert await service.repository.thread_exists(thread)
        assert file.read_text() == "untouched"
        await service.close()

    asyncio.run(check())


def test_directory_groups_cover_all_pages_and_exclude_archived(tmp_path):
    async def check():
        async def send(event):
            pass

        service = Service(tmp_path, send)
        try:
            first = tmp_path / "一/同名目录"
            second = tmp_path / "二/同名目录"
            for index in range(45):
                await service.repository.create_thread(ThreadId(str(index)), first)
            await service.repository.create_thread(ThreadId("older"), second)
            with closing(sqlite3.connect(service.database)) as db, db:
                db.execute("UPDATE threads SET updated_at='2000-01-01' WHERE id='older'")
            page = await service.list_threads({"limit": 40})
            assert len(page["threads"]) == 40
            assert {d["path"] for d in page["directories"]} == {str(first), str(second)}
            assert all(d["name"] == "同名目录" for d in page["directories"])
            group = await service.list_threads({"directory": str(first), "limit": 40})
            assert len(group["threads"]) == 40 and group["next"] == 40
            tail = await service.list_threads({"directory": str(first), "offset": 40})
            assert len(tail["threads"]) == 5 and tail["next"] is None
            assert [
                t["id"] for t in (await service.list_threads({"directory": str(second)}))["threads"]
            ] == ["older"]
            with closing(sqlite3.connect(service.database)) as db, db:
                db.execute("UPDATE threads SET archived_at='2026-01-01' WHERE id='older'")
            assert (await service.list_threads({}))["directories"] == [
                {"path": str(first), "name": "同名目录"}
            ]
        finally:
            await service.close()

    asyncio.run(check())


def test_delete_reminder_contract_survives_restart(tmp_path):
    async def check():
        async def send(event):
            pass

        service = Service(tmp_path, send)
        assert (await service.initialize({"protocolVersion": 1}))["threadManagementVersion"] == 2
        assert await service.read_state({"key": "deleteConfirmationUntil"}) is None
        await service.write_state({"key": "deleteConfirmationUntil", "value": 123})
        await service.close()
        service = Service(tmp_path, send)
        assert await service.read_state({"key": "deleteConfirmationUntil"}) == 123
        await service.close()

    asyncio.run(check())


def test_scheduled_threads_hidden_before_paging_and_directory_grouping(tmp_path):
    from corki.cron.store import CronStore
    from corki.protocol.session_source import SessionSource

    async def check():
        async def send(event):
            pass

        service = Service(tmp_path, send)
        try:
            cron = CronStore(tmp_path)
            job = cron.save({"name": "scheduled", "prompt": "task", "schedule": "every 1h",
                             "timezone": "UTC"})
            legacy = cron.claim(job)
            cron.start_run(legacy)
            await service.repository.create_thread(ThreadId(legacy), tmp_path / "only-cron")
            await service.repository.create_thread(
                ThreadId("new-cron"), tmp_path / "only-cron",
                session_source=SessionSource.from_startup_arg("cron"),
            )
            for identity in ("manual-1", "manual-2"):
                await service.repository.create_thread(ThreadId(identity), tmp_path / "interactive")
            # Put cron rows first so filtering after LIMIT would produce an empty page.
            with closing(sqlite3.connect(service.database)) as db, db:
                db.execute("UPDATE threads SET updated_at='2100-01-01' WHERE cwd=?",
                           (str(tmp_path / "only-cron"),))
            first = await service.list_threads({"limit": 1})
            second = await service.list_threads({"limit": 1, "offset": first["next"]})
            assert {first["threads"][0]["id"], second["threads"][0]["id"]} == {
                "manual-1", "manual-2"}
            assert second["next"] is None
            assert first["directories"] == [
                {"path": str(tmp_path / "interactive"), "name": "interactive"}]
            group = await service.list_threads({"directory": str(tmp_path / "only-cron")})
            assert not group["threads"]
            assert await service.repository.thread_exists(ThreadId(legacy))
            assert await service.repository.thread_exists(ThreadId("new-cron"))
            assert cron.run(legacy)["status"] == "running"
            # New provenance still filters a run after cron history has been pruned.
            assert all(r["id"] != "new-cron" for r in first["threads"])
        finally:
            await service.close()

    asyncio.run(check())
