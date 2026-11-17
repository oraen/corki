"""Durable identity is not execution; cleanup must not resurrect a worker."""

import asyncio
import sqlite3
import subprocess
import sys

import pytest

from corki.agents.controller import AgentController
from corki.agents.store import AgentStore, install_agent_schema
from corki.protocol.events import TurnCompleted
from corki.protocol.session_source import SessionSource, SubAgentSource, ThreadSpawnSource
from corki.storage.sqlite import SQLiteSessionRepository
from corki.storage.thread_delete import SQLiteThreadDeleteStore
from corki.storage.thread_writer import ThreadWriterConflict, ThreadWriterLease


def make_store(tmp_path, parent="parent"):
    path = tmp_path / "core.db"
    SQLiteSessionRepository(path)
    return AgentStore(path, parent)


def test_shutdown_restores_unloaded_identity_and_close_is_owned_and_repeatable(tmp_path):
    async def run():
        store = make_store(tmp_path)
        starts, closes = [], []

        class Runtime:
            async def stream(self, message, *, realtime):
                starts.append(message)
                yield TurnCompleted("child", "turn", "done")

            async def aclose(self):
                closes.append(True)

        async def factory(identity, context):
            return Runtime()

        owner = AgentController(factory, store=store)
        child = await owner.spawn("initial", object())
        await owner.wait([child["id"]], 1000)
        await owner.aclose()
        assert (await store.lookup(child["id"]))["state"] == "open"
        restored = AgentController(factory, store=store)
        await restored.restore()
        assert restored.get(child["id"]).runtime is None
        assert (await restored.wait([child["id"]], 0))["timed_out"] is False
        foreign = AgentController(factory, store=AgentStore(store.path, "other"))
        await foreign.restore()
        with pytest.raises(ValueError, match="Unknown"):
            await foreign.close(child["id"])
        with pytest.raises(ValueError, match="Unknown"):
            await restored.close("random-id")
        assert (await restored.close(child["id"]))["status"] == "closed"
        assert (await restored.close(child["id"]))["status"] == "closed"
        assert starts == ["initial"] and closes == [True]
        after = AgentController(factory, store=store)
        await after.restore()
        assert not after.children
        assert (await after.close(child["id"]))["status"] == "closed"
        with pytest.raises(ValueError, match="Unknown"):
            await after.send(child["id"], "never revive a closed child", object())
        await restored.aclose()
        await after.aclose()
        await foreign.aclose()

    asyncio.run(run())


def test_interrupted_work_is_not_replayed_and_resumption_requires_host_context(tmp_path):
    async def run():
        store = make_store(tmp_path)
        await store.create("child")  # Crash after durable admission, before terminal write.
        calls = []

        class Runtime:
            async def stream(self, message, *, realtime):
                calls.append(message)
                yield TurnCompleted("child", "turn", "done")

            async def aclose(self):
                pass

        async def factory(identity, context):
            assert context == "current authority"
            return Runtime()

        owner = AgentController(factory, store=store)
        await owner.restore()
        await owner.restore()  # Idempotent, not another DB-wide recovery.
        assert (await owner.list())["agents"][0]["status"] == "interrupted"
        assert calls == []
        with pytest.raises(ValueError, match="context"):
            await owner.send("child", "explicit new input")
        await owner.send("child", "explicit new input", "current authority")
        await owner.wait(["child"], 1000)
        assert calls == ["explicit new input"]
        await owner.aclose()

    asyncio.run(run())


def test_unloaded_children_do_not_exhaust_runtime_slots(tmp_path):
    async def run():
        store = make_store(tmp_path)
        for i in range(3):
            await store.create(str(i))

        async def factory(identity, context):
            await asyncio.Event().wait()

        owner = AgentController(factory, limit=1, store=store)
        await owner.restore()
        try:
            await owner.send("0", "explicit input", object())
            with pytest.raises(ValueError, match="limit"):
                await owner.send("1", "too many", object())
            with pytest.raises(ValueError, match="limit"):
                await owner.spawn("too many", object())
            await owner.close("0")
            await owner.spawn("available again", object())
        finally:
            await owner.aclose()

    asyncio.run(run())


def test_legacy_migration_uses_typed_provenance_and_never_reopens_closed_edges(tmp_path):
    store = make_store(tmp_path)
    source = SessionSource.subagent(SubAgentSource("thread_spawn", ThreadSpawnSource("parent", 1)))
    with sqlite3.connect(store.path) as db:
        db.execute("DELETE FROM schema_migrations WHERE name='agent_children_v1'")
        db.execute("INSERT INTO threads(id,cwd,source) VALUES ('legacy',?,?)",
                   (str(tmp_path), source.storage_value))
        db.execute("INSERT INTO threads(id,cwd,source) VALUES ('fake',?,'subagent:parent')",
                   (str(tmp_path),))
        install_agent_schema(db)
        assert db.execute("SELECT child_id FROM agent_children").fetchall() == [("legacy",)]
        db.execute("UPDATE agent_children SET state='closed'")
        install_agent_schema(db)
        assert db.execute("SELECT state FROM agent_children").fetchone() == ("closed",)
        plan = db.execute("EXPLAIN QUERY PLAN SELECT child_id FROM agent_children "
                          "WHERE parent_id=? AND state='open'", ("parent",)).fetchall()
        assert any("idx_agent_children_parent" in row[-1] for row in plan)


def test_physical_delete_cleans_identity_but_close_keeps_history(tmp_path):
    async def run():
        store = make_store(tmp_path)
        await store.create("child")
        with sqlite3.connect(store.path) as db:
            db.execute("INSERT INTO threads(id,cwd) VALUES ('child',?)", (str(tmp_path),))
        await store.update("child", "completed", close=True)
        with sqlite3.connect(store.path) as db:
            assert db.execute("SELECT 1 FROM threads WHERE id='child'").fetchone()
        await SQLiteThreadDeleteStore(store.path).delete("child")
        assert await store.lookup("child") is None

    asyncio.run(run())


def test_cleanup_is_joined_on_cancellation_and_concurrent_close_is_safe(tmp_path):
    async def run():
        store = make_store(tmp_path)
        cleanup, release = asyncio.Event(), asyncio.Event()
        closes = []

        class Runtime:
            async def stream(self, message, *, realtime):
                yield TurnCompleted("child", "turn", "done")

            async def aclose(self):
                closes.append(True)
                cleanup.set()
                await release.wait()

        async def factory(identity, context):
            return Runtime()

        owner = AgentController(factory, store=store)
        child = await owner.spawn("initial", object())
        await owner.wait([child["id"]], 1000)
        closing = asyncio.create_task(owner.close(child["id"]))
        await cleanup.wait()
        concurrent = asyncio.create_task(owner.close(child["id"]))
        closing.cancel()
        await asyncio.sleep(0)
        assert not closing.done()
        with pytest.raises(ValueError, match="closed"):
            await owner.send(child["id"], "cannot race cleanup", object())
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert (await concurrent)["status"] == "closed"
        assert closes == [True]
        assert not owner.children
        await owner.aclose()

    asyncio.run(run())


def test_failed_cleanup_is_retryable_and_does_not_claim_release(tmp_path):
    async def run():
        store = make_store(tmp_path)
        closes = []

        class Runtime:
            async def stream(self, message, *, realtime):
                yield TurnCompleted("child", "turn", "done")

            async def aclose(self):
                closes.append(True)
                if len(closes) == 1:
                    raise OSError("cleanup failed")

        async def factory(identity, context):
            return Runtime()

        owner = AgentController(factory, store=store)
        child = await owner.spawn("initial", object())
        await owner.wait([child["id"]], 1000)
        with pytest.raises(OSError, match="cleanup failed"):
            await owner.close(child["id"])
        assert owner.get(child["id"]).runtime is not None
        assert owner.get(child["id"]).stopping
        assert (await owner.close(child["id"]))["status"] == "closed"
        await owner.aclose()

    asyncio.run(run())


def test_admission_checks_canonical_ancestry_and_archival(tmp_path):
    async def run():
        store = make_store(tmp_path)
        await store.create("child")
        with sqlite3.connect(store.path) as db:
            db.execute("INSERT INTO threads(id,cwd,source) VALUES ('child',?,'vscode')",
                       (str(tmp_path),))
        with pytest.raises(ValueError, match="ancestry"):
            await store.admit("child")
        source = SessionSource.subagent(SubAgentSource(
            "thread_spawn", ThreadSpawnSource("parent", 1)
        ))
        with sqlite3.connect(store.path) as db:
            db.execute("UPDATE threads SET source=?,archived_at=CURRENT_TIMESTAMP",
                       (source.storage_value,))
        with pytest.raises(ValueError, match="archived"):
            await store.admit("child")
        with sqlite3.connect(store.path) as db:
            db.execute("UPDATE threads SET archived_at=NULL")
        await store.admit("child")
        await store.update("child", "completed", close=True)
        with pytest.raises(ValueError, match="closed"):
            await store.admit("child")

    asyncio.run(run())


def test_abrupt_process_exit_keeps_identity_but_never_replays_work(tmp_path):
    script = '''
import asyncio, os, pathlib, sys
from corki.agents.controller import AgentController
from corki.agents.store import AgentStore
from corki.storage.sqlite import SQLiteSessionRepository
async def main():
    path = pathlib.Path(sys.argv[1])
    SQLiteSessionRepository(path)
    entered = asyncio.Event()
    async def factory(identity, context):
        entered.set()
        await asyncio.Event().wait()
    owner = AgentController(factory, store=AgentStore(path, "parent"))
    child = await owner.spawn("must not replay", None)
    await entered.wait()
    print(child["id"], flush=True)
    os._exit(0)
asyncio.run(main())
'''
    path = tmp_path / "core.db"
    child_id = subprocess.check_output(
        [sys.executable, "-c", script, str(path)], text=True, timeout=20,
    ).strip()

    async def run():
        async def factory(identity, context):
            pytest.fail("Closing an identity after process death must not launch a runtime")

        owner = AgentController(factory, store=AgentStore(path, "parent"))
        await owner.restore()
        assert owner.get(child_id).status == "interrupted"
        assert (await owner.close(child_id))["status"] == "closed"
        await owner.aclose()

    asyncio.run(run())


def test_closing_unloaded_child_does_not_claim_success_over_a_live_writer(tmp_path):
    async def run():
        store = make_store(tmp_path)
        await store.create("child")

        async def factory(identity, context):
            pytest.fail("close must not instantiate a child")

        owner = AgentController(factory, store=store)
        await owner.restore()
        other = ThreadWriterLease(store.path, "child")
        await other.acquire()
        try:
            with pytest.raises(ThreadWriterConflict):
                await owner.close("child")
            assert (await store.lookup("child"))["state"] == "open"
        finally:
            await other.aclose()
        assert (await owner.close("child"))["status"] == "closed"
        await owner.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("cancel_spawn", [False, True])
def test_pending_spawn_rollback_does_not_leave_running_or_replayable_work(tmp_path, cancel_spawn):
    async def run():
        store = make_store(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        create = store.create

        async def delayed_create(identity):
            await create(identity)
            entered.set()
            await release.wait()

        store.create = delayed_create

        async def factory(identity, context):
            pytest.fail("Cancelled admission must not start a worker")

        owner = AgentController(factory, store=store)
        spawning = asyncio.create_task(owner.spawn("initial", object()))
        await entered.wait()
        if cancel_spawn:
            spawning.cancel()
            with pytest.raises(asyncio.CancelledError):
                await spawning
        else:
            stopping = asyncio.create_task(owner.stop_all())
            await asyncio.sleep(0)
            release.set()
            with pytest.raises(ValueError, match="closed"):
                await spawning
            await stopping
        assert not owner.children
        with sqlite3.connect(store.path) as db:
            assert db.execute("SELECT state FROM agent_children").fetchall() == [("closed",)]
        await owner.aclose()

    asyncio.run(run())
