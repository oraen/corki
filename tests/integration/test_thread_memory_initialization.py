"""Source mode is creation metadata, not a post-insert patch or a resume override."""

import asyncio
import json
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
from corki.protocol.memory import ThreadMemoryMode
from corki.storage import SQLiteSessionRepository


class Main:
    def __init__(self, database, expected):
        self.database, self.expected = database, expected
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        with sqlite3.connect(self.database) as db:
            assert db.execute("SELECT memory_mode FROM threads").fetchone() == (self.expected,)
        yield ModelCompleted(
            (AssistantMessageItem("ready", request.items[-1].turn_id, new_step_id()),)
        )

    async def aclose(self):
        pass


class Memory:
    async def stream(self, request):
        yield ModelCompleted(
            (
                AssistantMessageItem(
                    json.dumps({"memory": "memory", "memory_summary": "routing", "skills": []}),
                    request.items[-1].turn_id,
                    new_step_id(),
                ),
            )
        )

    async def aclose(self):
        pass


async def _runtime(tmp_path, *, generate, enabled=False, use=True, thread_id=None, expected=None):
    database = tmp_path / "history.db"
    return await LangGraphRuntime.acreate(
        settings=CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            memories_enabled=enabled,
            memories_generate=generate,
            memories_use=use,
        ),
        database_path=database,
        home_path=tmp_path / "home",
        memory_root=tmp_path / "memories",
        thread_id=thread_id,
        model=Main(database, expected or ("enabled" if generate else "disabled")),
        memory_model=Memory(),
    )


@pytest.mark.parametrize("generate", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("use", [False, True])
def test_initial_mode_tracks_generate_not_feature_or_recall_and_survives_reopen(
    tmp_path, generate, enabled, use
):
    async def scenario():
        expected = "enabled" if generate else "disabled"
        runtime = await _runtime(tmp_path, generate=generate, enabled=enabled, use=use)
        try:
            for text in ("first", "second"):
                events = [e async for e in runtime.stream(text)]
                assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(runtime._model.requests) == 2
            history = await runtime._repository.load_items(runtime.thread_id)
        finally:
            await runtime.aclose()
        cold = await _runtime(
            tmp_path, generate=not generate, thread_id=runtime.thread_id, expected=expected
        )
        try:
            assert isinstance([e async for e in cold.stream("reopen")][-1], TurnCompleted)
            after = await cold._repository.load_items(cold.thread_id)
            assert all(item in after for item in history)
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["enabled", "disabled", "polluted"])
def test_stored_mode_is_authoritative_when_runtime_config_changes(tmp_path, mode):
    async def scenario():
        database, thread = tmp_path / "history.db", new_thread_id()
        sessions = SQLiteSessionRepository(database)
        await sessions.create_thread(thread, tmp_path)
        with sqlite3.connect(database) as db:
            db.execute("UPDATE threads SET memory_mode=?", (mode,))
        for generate in (False, True):
            runtime = await _runtime(tmp_path, generate=generate, thread_id=thread, expected=mode)
            try:
                assert isinstance([e async for e in runtime.stream("resume")][-1], TurnCompleted)
            finally:
                await runtime.aclose()

    asyncio.run(scenario())


def test_disabled_creation_is_not_claimable_by_another_host_until_explicit_enable(tmp_path):
    async def scenario():
        runtime = await _runtime(tmp_path, generate=False)
        database = tmp_path / "history.db"
        memory = SQLiteMemoryRepository(database)
        try:
            events = [e async for e in runtime.stream("private source")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            with sqlite3.connect(database) as db:
                db.execute(
                    "UPDATE threads SET updated_at=?",
                    ((datetime.now(UTC) - timedelta(hours=2)).isoformat(),),
                )
            options = dict(
                current_thread_id=new_thread_id(),
                max_age_days=30,
                min_idle_hours=0,
                limit=10,
                lease_seconds=600,
            )
            assert not await memory.claim_extraction_jobs(**options)
            await runtime.set_thread_memory_mode("enabled")
            claims = await memory.claim_extraction_jobs(**options)
            assert len(claims) == 1 and claims[0].thread_id == runtime.thread_id
        finally:
            await memory.close()
            await runtime.aclose()

    asyncio.run(scenario())


def test_creation_commit_exposes_final_mode_before_runtime_can_patch_it(tmp_path, monkeypatch):
    async def scenario():
        runtime = await _runtime(tmp_path, generate=False)
        entered, release = asyncio.Event(), threading.Event()
        other = SQLiteSessionRepository(tmp_path / "history.db")
        loop = asyncio.get_running_loop()
        create = runtime._repository._create_thread

        def held(*args):
            create(*args)
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)

        monkeypatch.setattr(runtime._repository, "_create_thread", held)

        async def consume():
            return [e async for e in runtime.stream("work")]

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            assert not runtime._model.requests
            await other.create_thread(
                runtime.thread_id, tmp_path / "different-cwd", memory_mode=ThreadMemoryMode.ENABLED
            )
            with sqlite3.connect(tmp_path / "history.db") as db:
                assert db.execute("SELECT memory_mode,cwd FROM threads").fetchone() == (
                    "disabled",
                    str(tmp_path.resolve()),
                )
        finally:
            release.set()
            events = await task
            await runtime.aclose()
        assert isinstance(events[-1], TurnCompleted), events[-1]

    asyncio.run(scenario())


def test_initial_mode_insert_failure_rolls_back_and_can_retry(tmp_path):
    async def scenario():
        database, thread = tmp_path / "history.db", new_thread_id()
        sessions = SQLiteSessionRepository(database)
        with sqlite3.connect(database) as db:
            db.execute(
                "CREATE TRIGGER reject_creation BEFORE INSERT ON threads "
                "BEGIN SELECT RAISE(ABORT, 'creation failed'); END"
            )
        with pytest.raises(sqlite3.IntegrityError, match="creation failed"):
            await sessions.create_thread(thread, tmp_path, memory_mode=ThreadMemoryMode.DISABLED)
        with sqlite3.connect(database) as db:
            assert db.execute("SELECT count(*) FROM threads").fetchone() == (0,)
            db.execute("DROP TRIGGER reject_creation")
        await sessions.create_thread(thread, tmp_path, memory_mode=ThreadMemoryMode.DISABLED)
        await sessions.create_thread(thread, tmp_path / "ignored")
        with sqlite3.connect(database) as db:
            assert db.execute("SELECT memory_mode,cwd FROM threads").fetchone() == (
                "disabled",
                str(tmp_path.resolve()),
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["polluted", True])
def test_invalid_initial_mode_does_not_create_a_thread(tmp_path, mode):
    async def scenario():
        database = tmp_path / "history.db"
        sessions = SQLiteSessionRepository(database)
        with pytest.raises(ValueError):
            await sessions.create_thread(new_thread_id(), tmp_path, memory_mode=mode)
        with sqlite3.connect(database) as db:
            assert db.execute("SELECT count(*) FROM threads").fetchone() == (0,)

    asyncio.run(scenario())


def test_custom_creation_adapter_cannot_silently_ignore_source_metadata(tmp_path, monkeypatch):
    async def scenario():
        runtime = await _runtime(tmp_path, generate=False)
        create = runtime._repository.create_thread
        calls = []

        async def legacy(thread, cwd):
            calls.append(thread)
            await create(thread, cwd)

        monkeypatch.setattr(runtime._repository, "create_thread", legacy)
        try:
            with pytest.raises(TypeError, match="memory_mode"):
                _ = [e async for e in runtime.stream("work")]
            assert not calls and not runtime._model.requests
            with sqlite3.connect(tmp_path / "history.db") as db:
                assert db.execute("SELECT count(*) FROM threads").fetchone() == (0,)
            monkeypatch.setattr(runtime._repository, "create_thread", create)
            assert isinstance([e async for e in runtime.stream("retry")][-1], TurnCompleted)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
