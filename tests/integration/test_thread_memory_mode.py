"""Public metadata control reaches the real runtime without becoming model input."""

import asyncio
import sqlite3
import threading
from datetime import UTC, datetime, timedelta

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory import SQLiteMemoryRepository
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_thread_id
from corki.protocol.items import AssistantMessageItem, new_step_id
from corki.storage import SQLiteSessionRepository


class Main:
    def __init__(self):
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        yield ModelCompleted(
            (AssistantMessageItem("ready", request.items[-1].turn_id, new_step_id()),)
        )

    async def aclose(self):
        pass


async def _runtime(tmp_path, *, enabled=False, thread_id=None):
    return await LangGraphRuntime.acreate(
        settings=CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            memories_enabled=enabled,
            memories_generate=False,
            memories_background_enabled=False,
        ),
        database_path=tmp_path / "history.db",
        home_path=tmp_path / "home",
        memory_root=tmp_path / "memories",
        model=Main(),
        thread_id=thread_id,
    )


def _mode(database, thread_id):
    with sqlite3.connect(database) as db:
        return db.execute("SELECT memory_mode FROM threads WHERE id=?", (thread_id,)).fetchone()


@pytest.mark.parametrize("enabled", [False, True])
def test_current_mode_before_sampling_persists_across_turns_and_reopen(tmp_path, enabled):
    async def scenario():
        runtime = await _runtime(tmp_path, enabled=enabled)
        database = tmp_path / "history.db"
        root = tmp_path / "memories"
        root.mkdir(exist_ok=True)
        summary = root / "memory_summary.md"
        summary.write_text("v1\nRETAINED_ROUTING")
        try:
            await runtime.set_thread_memory_mode("disabled")
            assert not runtime._model.requests
            assert _mode(database, runtime.thread_id) == ("disabled",)
            assert await runtime._repository.load_items(runtime.thread_id) == ()
            for message in ("first", "second"):
                assert isinstance([e async for e in runtime.stream(message)][-1], TurnCompleted)
                assert _mode(database, runtime.thread_id) == ("disabled",)
            history = await runtime._repository.load_items(runtime.thread_id)
            with sqlite3.connect(database) as db:
                version = db.execute("SELECT updated_at FROM threads").fetchone()
            await runtime.set_thread_memory_mode("enabled")
            await runtime.set_thread_memory_mode("enabled")
            assert await runtime._repository.load_items(runtime.thread_id) == history
            with sqlite3.connect(database) as db:
                assert db.execute("SELECT updated_at FROM threads").fetchone() == version
            assert summary.read_text() == "v1\nRETAINED_ROUTING"
            assert runtime._settings.memories_generate is False
            if enabled:
                assert any(
                    "RETAINED_ROUTING" in i.content
                    for i in runtime._model.requests[0].items
                    if getattr(i, "key", None) == "memory.instructions"
                )
        finally:
            await runtime.aclose()
        cold = await _runtime(tmp_path, enabled=enabled, thread_id=runtime.thread_id)
        try:
            await cold.set_thread_memory_mode("disabled")
            assert await cold._repository.load_items(cold.thread_id) == history
            assert _mode(database, cold.thread_id) == ("disabled",)
        finally:
            await cold.aclose()

    asyncio.run(scenario())


def test_stored_target_missing_and_invalid_requests_do_not_create_threads(tmp_path):
    async def scenario():
        runtime = await _runtime(tmp_path)
        database = tmp_path / "history.db"
        stored, missing = new_thread_id(), new_thread_id()
        await runtime._repository.create_thread(stored, tmp_path)
        try:
            for mode in ("disabled", "enabled"):
                await runtime.set_thread_memory_mode(mode, thread_id=stored.upper())
                assert _mode(database, stored) == (mode,)
            with pytest.raises(LookupError, match="thread"):
                await runtime.set_thread_memory_mode("disabled", thread_id=missing)
            for thread_id in ("", "not-a-uuid"):
                with pytest.raises(ValueError, match="thread"):
                    await runtime.set_thread_memory_mode("disabled", thread_id=thread_id)
            for mode in ("polluted", "Enabled", "", True):
                with pytest.raises(ValueError, match="mode"):
                    await runtime.set_thread_memory_mode(mode)
            with sqlite3.connect(database) as db:
                assert db.execute("SELECT id FROM threads").fetchall() == [(stored,)]
            assert not runtime._model.requests
        finally:
            await runtime.aclose()
        with pytest.raises(RuntimeError, match="closed"):
            await runtime.set_thread_memory_mode("enabled")

    asyncio.run(scenario())


def test_mode_gates_claim_and_selection_without_rewriting_extraction_state(tmp_path):
    async def scenario():
        runtime = await _runtime(tmp_path)
        database = tmp_path / "history.db"
        memory = SQLiteMemoryRepository(database)
        try:
            assert isinstance([e async for e in runtime.stream("source")][-1], TurnCompleted)
            await runtime.set_thread_memory_mode("enabled")
            version = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
            prior = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
            with sqlite3.connect(database) as db:
                db.execute("UPDATE threads SET updated_at=?", (version,))
                db.execute(
                    "INSERT INTO memory_stage1_outputs "
                    "(thread_id,cwd,source_updated_at,raw_memory,rollout_summary) "
                    "VALUES (?,?,?,?,?)",
                    (runtime.thread_id, str(tmp_path), prior, "raw", "summary"),
                )
            selected = await memory.load_consolidation_inputs(limit=10, max_unused_days=30)
            assert len(selected) == 1
            with sqlite3.connect(database) as db:
                output = db.execute("SELECT * FROM memory_stage1_outputs").fetchall()
                jobs = db.execute("SELECT * FROM memory_jobs").fetchall()
            await runtime.set_thread_memory_mode("disabled")
            assert not await memory.load_consolidation_inputs(limit=10, max_unused_days=30)
            assert not await memory.claim_extraction_jobs(
                current_thread_id=new_thread_id(),
                limit=10,
                min_idle_hours=0,
                max_age_days=30,
                lease_seconds=600,
            )
            with sqlite3.connect(database) as db:
                assert db.execute("SELECT * FROM memory_stage1_outputs").fetchall() == output
                assert db.execute("SELECT * FROM memory_jobs").fetchall() == jobs
                assert db.execute("SELECT updated_at FROM threads").fetchone() == (version,)
            await runtime.set_thread_memory_mode("enabled")
            assert await memory.load_consolidation_inputs(limit=10, max_unused_days=30) == selected
            claims = await memory.claim_extraction_jobs(
                current_thread_id=new_thread_id(),
                limit=10,
                min_idle_hours=0,
                max_age_days=30,
                lease_seconds=600,
            )
            assert len(claims) == 1 and claims[0].source_updated_at == version
        finally:
            await memory.close()
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["create", "update"])
def test_mode_write_cancellation_and_close_join_started_database_worker(
    tmp_path, monkeypatch, phase
):
    async def scenario():
        runtime = await _runtime(tmp_path)
        if phase == "update":
            await runtime.set_thread_memory_mode("enabled")
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        method = "_create_thread" if phase == "create" else "_set_thread_memory_mode"
        write = getattr(runtime._repository, method)

        def held(*args):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            return write(*args)

        monkeypatch.setattr(runtime._repository, method, held)
        update = asyncio.create_task(runtime.set_thread_memory_mode("disabled"))
        closing = None
        try:
            await asyncio.wait_for(entered.wait(), 2)
            update.cancel()
            await asyncio.sleep(0)
            update.cancel()
            closing = asyncio.create_task(runtime.aclose())
            done, _ = await asyncio.wait((update, closing), timeout=0.03)
            assert not done
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await update
            if closing is not None:
                await closing
            await runtime.aclose()
        assert _mode(tmp_path / "history.db", runtime.thread_id) == ("disabled",)

    asyncio.run(scenario())


def test_mode_schema_migrates_legacy_threads_without_initializing_memory_jobs(tmp_path):
    database = tmp_path / "history.db"
    thread = new_thread_id()
    with sqlite3.connect(database) as db:
        db.execute(
            "CREATE TABLE threads(id TEXT PRIMARY KEY,cwd TEXT NOT NULL,"
            "created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        db.execute("INSERT INTO threads(id,cwd) VALUES (?,?)", (thread, str(tmp_path)))

    async def scenario():
        runtime = await _runtime(tmp_path)
        try:
            assert _mode(database, thread) == ("enabled",)
            await runtime.set_thread_memory_mode("disabled", thread_id=thread)
            SQLiteSessionRepository(database)
            assert _mode(database, thread) == ("disabled",)
            with sqlite3.connect(database) as db:
                assert not db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_jobs'"
                ).fetchall()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_mode_sql_failure_rolls_back_and_runtime_can_retry(tmp_path):
    async def scenario():
        runtime = await _runtime(tmp_path)
        database = tmp_path / "history.db"
        try:
            await runtime.set_thread_memory_mode("enabled")
            with sqlite3.connect(database) as db:
                db.execute(
                    "CREATE TRIGGER reject_mode BEFORE UPDATE OF memory_mode ON threads "
                    "BEGIN SELECT RAISE(ABORT, 'mode rejected'); END"
                )
            with pytest.raises(sqlite3.IntegrityError, match="mode rejected"):
                await runtime.set_thread_memory_mode("disabled")
            assert _mode(database, runtime.thread_id) == ("enabled",)
            assert not runtime._model.requests
            with sqlite3.connect(database) as db:
                db.execute("DROP TRIGGER reject_mode")
            await runtime.set_thread_memory_mode("disabled")
            assert _mode(database, runtime.thread_id) == ("disabled",)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_unsupported_session_repository_fails_before_materializing_thread(tmp_path, monkeypatch):
    async def scenario():
        runtime = await _runtime(tmp_path)
        monkeypatch.setattr(runtime._repository, "set_thread_memory_mode", None)
        try:
            with pytest.raises(RuntimeError, match="does not support"):
                await runtime.set_thread_memory_mode("disabled")
            with sqlite3.connect(tmp_path / "history.db") as db:
                assert db.execute("SELECT count(*) FROM threads").fetchone() == (0,)
            assert not runtime._model.requests
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("stop", ["cancel_waiter", "close_runtime"])
def test_waiting_mode_operation_cannot_outlive_admission(tmp_path, monkeypatch, stop):
    async def scenario():
        runtime = await _runtime(tmp_path)
        await runtime.set_thread_memory_mode("enabled")
        entered, release = asyncio.Event(), asyncio.Event()
        setter = runtime._repository.set_thread_memory_mode

        async def held(*args):
            entered.set()
            await release.wait()
            return await setter(*args)

        monkeypatch.setattr(runtime._repository, "set_thread_memory_mode", held)
        first = asyncio.create_task(runtime.set_thread_memory_mode("disabled"))
        queued = closing = None
        try:
            await asyncio.wait_for(entered.wait(), 2)
            queued = asyncio.create_task(runtime.set_thread_memory_mode("enabled"))
            await asyncio.sleep(0)
            if stop == "cancel_waiter":
                queued.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await queued
            else:
                closing = asyncio.create_task(runtime.aclose())
                done, _ = await asyncio.wait((first, queued, closing), timeout=0.03)
                assert not done
            release.set()
            await first
            if stop == "close_runtime":
                with pytest.raises(RuntimeError, match="closed"):
                    await queued
                await closing
            assert _mode(tmp_path / "history.db", runtime.thread_id) == ("disabled",)
        finally:
            release.set()
            await asyncio.gather(
                *(t for t in (first, queued, closing) if t is not None), return_exceptions=True
            )
            await runtime.aclose()

    asyncio.run(scenario())


def test_runtime_close_rejects_queued_metadata_before_active_turn_finishes(tmp_path, monkeypatch):
    async def scenario():
        runtime = await _runtime(tmp_path)
        sampling, cancelling, model_release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        writing, write_release = asyncio.Event(), asyncio.Event()
        setter = runtime._repository.set_thread_memory_mode

        async def stream(request):
            sampling.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelling.set()
                await model_release.wait()
                raise
            if False:
                yield None

        async def held(*args):
            writing.set()
            await write_release.wait()
            return await setter(*args)

        async def consume():
            return [event async for event in runtime.stream("work")]

        monkeypatch.setattr(runtime._model, "stream", stream)
        monkeypatch.setattr(runtime._repository, "set_thread_memory_mode", held)
        turn = asyncio.create_task(consume())
        first = queued = closing = None
        try:
            await asyncio.wait_for(sampling.wait(), 2)
            first = asyncio.create_task(runtime.set_thread_memory_mode("disabled"))
            await asyncio.wait_for(writing.wait(), 2)
            queued = asyncio.create_task(runtime.set_thread_memory_mode("enabled"))
            await asyncio.sleep(0)
            closing = asyncio.create_task(runtime.aclose())
            await asyncio.wait_for(cancelling.wait(), 2)
            write_release.set()
            await first
            with pytest.raises(RuntimeError, match="closed"):
                await queued
            assert _mode(tmp_path / "history.db", runtime.thread_id) == ("disabled",)
            assert not closing.done()
        finally:
            write_release.set()
            model_release.set()
            await asyncio.gather(
                *(t for t in (turn, first, queued, closing) if t is not None),
                return_exceptions=True,
            )
            await runtime.aclose()

    asyncio.run(scenario())
