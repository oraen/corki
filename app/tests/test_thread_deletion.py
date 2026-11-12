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
            transcript = await service.repository.materialize_transcript(ThreadId(thread))
            assert transcript is not None
            with closing(sqlite3.connect(service.database)) as db:
                turn = db.execute("SELECT id FROM turns WHERE thread_id=?", (thread,)).fetchone()[0]
            for file, tables in (
                ("checkpoints.db", ("checkpoints", "writes", "checkpoint_objects")),
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
                    # Independent accounting survives physical conversation deletion.
                    if "thread_id" in columns and table not in {"usage_events", "usage_threads"}:
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


def test_robot_threads_have_separate_paginated_section_and_no_workspace_duplicates(tmp_path):
    from corki.protocol.session_source import SessionSource

    async def check():
        async def send(_):
            pass

        service = Service(tmp_path, send)
        try:
            common = tmp_path / "shared"
            bot_only = tmp_path / "robot-only"
            for index in range(42):
                await service.repository.create_thread(
                    ThreadId(f"robot-{index:02}"), common if index % 2 else bot_only,
                    session_source=SessionSource.from_startup_arg("gateway"),
                )
            await service.repository.create_thread(ThreadId("manual"), common)
            await service.repository.create_thread(
                ThreadId("scheduled"), bot_only,
                session_source=SessionSource.from_startup_arg("cron"),
            )
            with closing(sqlite3.connect(service.database)) as db, db:
                db.execute("UPDATE threads SET updated_at='2100-01-01' WHERE id LIKE 'robot-%'")
            recent = await service.list_threads({"limit": 1})
            assert [t["id"] for t in recent["threads"]] == ["manual"]
            assert recent["next"] is None
            assert recent["directories"] == [{"path": str(common), "name": "shared"}]
            workspace = await service.list_threads({"directory": str(common)})
            assert [t["id"] for t in workspace["threads"]] == ["manual"]
            assert not (await service.list_threads({"directory": str(bot_only)}))["threads"]
            first = await service.list_threads({"group": "robot", "limit": 40})
            assert len(first["threads"]) == 40 and first["next"] == 40
            assert first["directories"] == []
            tail = await service.list_threads({"group": "robot", "offset": first["next"]})
            assert len(tail["threads"]) == 2 and tail["next"] is None
            assert len({r["id"] for r in first["threads"] + tail["threads"]}) == 42
            with closing(sqlite3.connect(service.database)) as db, db:
                db.execute("UPDATE threads SET archived_at='2100-01-01' WHERE id='robot-00'")
            remaining = await service.list_threads({"group": "robot", "limit": 100})
            assert len(remaining["threads"]) == 41
            with pytest.raises(ValueError, match="Invalid session group"):
                await service.list_threads({"group": "unknown"})
            # Grouping is a read-only projection; all core records remain untouched.
            assert await service.repository.thread_exists(ThreadId("robot-00"))
        finally:
            await service.close()

    asyncio.run(check())


def test_robot_titles_use_existing_nickname_and_preserve_manual_names(tmp_path):
    from robot_history import sender_titles

    from corki.protocol.session_source import SessionSource

    async def check():
        async def send(_):
            pass

        service = Service(tmp_path, send)
        try:
            for identity in ("individual", "shared", "legacy-shared", "unnamed", "renamed"):
                await service.repository.create_thread(
                    ThreadId(identity), tmp_path,
                    session_source=SessionSource.from_startup_arg("gateway"),
                )
            assert sender_titles(tmp_path, ["individual"]) == {}
            gateway = tmp_path / "gateway/gateway.db"
            gateway.parent.mkdir(exist_ok=True)
            with closing(sqlite3.connect(gateway)) as db, db:
                db.executescript("""
                    CREATE TABLE user_sessions(thread TEXT UNIQUE,name TEXT);
                    CREATE TABLE routes(thread TEXT,sender_name TEXT,
                        last_seen REAL,binding_id TEXT);
                    CREATE TABLE conversations(id TEXT,thread TEXT);
                    INSERT INTO user_sessions VALUES('individual','小林'),('renamed','昵称');
                    INSERT INTO routes VALUES('individual','旧昵称',1,NULL),
                        ('shared','旧发送人',1,NULL),('shared','新发送人',2,'binding');
                    INSERT INTO conversations VALUES('binding','legacy-shared');
                """)
            service.state.rename("renamed", "手动设置的名字")
            page = await service.list_threads({"group": "robot"})
            names = {row["id"]: row["title"] for row in page["threads"]}
            assert names == {"individual": "小林", "shared": "新发送人",
                             "unnamed": "机器人会话", "renamed": "手动设置的名字",
                             "legacy-shared": "新发送人"}
            # Existing session names update from current sender metadata, no new turn needed.
            with closing(sqlite3.connect(gateway)) as db, db:
                db.execute("UPDATE user_sessions SET name='最新昵称' WHERE thread='individual'")
            refreshed = await service.list_threads({"group": "robot"})
            individual = next(r for r in refreshed["threads"] if r["id"] == "individual")
            assert individual["title"] == "最新昵称"
            with closing(sqlite3.connect(gateway)) as db, db:
                db.execute("DROP TABLE user_sessions")
            assert sender_titles(tmp_path, ["shared"])["shared"] == "新发送人"
        finally:
            await service.close()

    asyncio.run(check())


def test_internal_worker_sessions_never_leak_into_desktop_navigation(tmp_path):
    from corki.protocol.session_source import SessionSource, SubAgentSource, ThreadSpawnSource

    async def check():
        async def send(_):
            pass

        service = Service(tmp_path, send)
        try:
            common = tmp_path / 'common'
            worker_only = tmp_path / 'workers'
            await service.repository.create_thread(ThreadId('manual-parent'), common)
            await service.repository.create_thread(
                ThreadId('robot-parent'), common,
                session_source=SessionSource.from_startup_arg('gateway'),
            )
            for parent in ('manual-parent', 'robot-parent'):
                for index in range(3):
                    await service.repository.create_thread(
                        ThreadId(f'{parent}-child-{index}'), worker_only,
                        session_source=SessionSource.subagent(SubAgentSource(
                            'thread_spawn', ThreadSpawnSource(ThreadId(parent), 1),
                        )),
                    )
            await service.repository.create_thread(
                ThreadId('background-worker'), worker_only,
                session_source=SessionSource.internal('memory_consolidation'),
            )
            with closing(sqlite3.connect(service.database)) as db, db:
                db.execute("UPDATE threads SET updated_at='2100-01-01' WHERE cwd=?",
                           (str(worker_only),))
            recent = await service.list_threads({'limit': 1})
            assert [t['id'] for t in recent['threads']] == ['manual-parent']
            assert recent['next'] is None
            assert recent['directories'] == [{'path': str(common), 'name': 'common'}]
            assert not (await service.list_threads({'directory': str(worker_only)}))['threads']
            robots = await service.list_threads({'group': 'robot', 'limit': 1})
            assert [t['id'] for t in robots['threads']] == ['robot-parent']
            assert robots['next'] is None
            assert await service.repository.thread_exists(ThreadId('robot-parent-child-0'))
            # A user-supplied source label is not typed internal provenance.
            await service.repository.create_thread(
                ThreadId('custom-label'), common,
                session_source=SessionSource.from_startup_arg('subagent'),
            )
            assert 'custom-label' in {
                t['id'] for t in (await service.list_threads({}))['threads']
            }
        finally:
            await service.close()

    asyncio.run(check())


def test_workspace_display_aliases_persist_without_changing_paths(tmp_path):
    async def check():
        async def send(_):
            pass
        service=Service(tmp_path,send)
        first=tmp_path/'one'/'same-name'
        second=tmp_path/'two'/'same-name'
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        marker=first/'keep.txt'
        marker.write_text('unchanged')
        try:
            await service.repository.create_thread(ThreadId('first-workspace'),first)
            await service.repository.create_thread(ThreadId('second-workspace'),second)
            for path,name in ((first,'  自定义目录  '),(second,'另一个目录')):
                await service.write_state({'key':'directoryAlias:'+str(path),'value':name})
            listing=await service.list_threads({})
            names={row['path']:row['name'] for row in listing['directories']}
            assert names=={str(first):'自定义目录',str(second):'另一个目录'}
            assert {r['cwd'] for r in listing['threads']}=={str(first),str(second)}
            assert marker.read_text()=='unchanged'
        finally:
            await service.close()
        service=Service(tmp_path,send)
        try:
            listing=await service.list_threads({})
            names={row['path']:row['name'] for row in listing['directories']}
            assert names[str(first)]=='自定义目录'
            await service.write_state({'key':'directoryAlias:'+str(first),'value':'   '})
            listing=await service.list_threads({})
            names={row['path']:row['name'] for row in listing['directories']}
            assert names[str(first)]=='same-name'
            assert names[str(second)]=='另一个目录'
            for bad in (None,{},'x'*201,'line\nbreak'):
                with pytest.raises(ValueError):
                    await service.write_state({'key':'directoryAlias:'+str(first),'value':bad})
            assert marker.read_text()=='unchanged'
        finally:
            await service.close()
    asyncio.run(check())


def test_legacy_robot_ownership_filters_before_pagination_and_workspaces(tmp_path):
    async def check():
        async def send(_):
            pass
        service = Service(tmp_path, send)
        try:
            for thread in ('legacy-bot', 'manual'):
                await service.repository.create_thread(ThreadId(thread), tmp_path / thread)
            gateway = tmp_path / 'gateway'
            gateway.mkdir()
            with closing(sqlite3.connect(gateway / 'gateway.db')) as db, db:
                db.execute('CREATE TABLE user_usage_threads(thread TEXT PRIMARY KEY, channel TEXT, sender TEXT)')
                db.execute("INSERT INTO user_usage_threads VALUES('legacy-bot','fixture','fixture')")
            recent = await service.list_threads({'limit': 1})
            assert [row['id'] for row in recent['threads']] == ['manual']
            assert recent['next'] is None
            assert [row['path'] for row in recent['directories']] == [str(tmp_path / 'manual')]
            assert not (await service.list_threads({'directory': str(tmp_path / 'legacy-bot')}))['threads']
            robots = await service.list_threads({'group': 'robot'})
            assert [row['id'] for row in robots['threads']] == ['legacy-bot']
        finally:
            await service.close()
    asyncio.run(check())
