"""Historical source eligibility and current startup authority are separate contracts."""

import asyncio
import sqlite3
import threading

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id


class MainModel:
    async def stream(self, request):
        yield ModelCompleted(
            (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
        )

    async def aclose(self):
        pass


class MemoryModel(MainModel):
    def __init__(self):
        self.extractions = []

    async def stream(self, request):
        if request.output_schema is not None:
            self.extractions.append(request)
            text = '{"raw_memory":"","rollout_summary":"","rollout_slug":null}'
        else:
            text = (
                '{"memory":"No supported preferences.",'
                '"memory_summary":"No preferences indexed.","skills":[]}'
            )
        yield ModelCompleted(
            (AssistantMessageItem(text, request.items[-1].turn_id, new_step_id()),)
        )


@pytest.mark.parametrize(
    "source_name,expected",
    [
        ("cli", 1),
        ("vscode", 1),
        # Pinned Codex stores Custom as JSON but filters by Display strings.
        # These canonical local rows do not match the startup allowlist.
        ("atlas", 0),
        ("chatgpt", 0),
        ("exec", 0),
        ("mcp", 0),
        ("unknown", 0),
        ("custom-host", 0),
    ],
)
def test_real_source_runtime_controls_later_extraction(tmp_path, source_name, expected):
    from corki.protocol.session_source import SessionSource

    async def scenario():
        database = tmp_path / "history.db"
        settings = CorkiSettings(working_directory=tmp_path, skills_enabled=False)
        source = LangGraphRuntime.create(
            settings=settings,
            database_path=database,
            home_path=tmp_path / "home",
            session_source=SessionSource.from_startup_arg(source_name),
            model=MainModel(),
        )
        try:
            events = [event async for event in source.stream("Use pytest for this project.")]
            assert isinstance(events[-1], TurnCompleted)
        finally:
            await source.aclose()
        memory_model = MemoryModel()
        foreground = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_min_thread_idle_hours=0,
            ),
            database_path=database,
            home_path=tmp_path / "home",
            memory_root=tmp_path / "memories",
            model=MainModel(),
            memory_model=memory_model,
        )
        try:
            events = [event async for event in foreground.stream("foreground")]
            assert isinstance(events[-1], TurnCompleted)
            report = await foreground._memory_service.wait()
            assert report.claimed == expected
            assert len(memory_model.extractions) == expected
            assert not report.failed, foreground._memory_service.warnings
            assert not foreground._memory_service.warnings
        finally:
            await foreground.aclose()

    asyncio.run(scenario())


def test_layout_failure_is_background_only_and_precedes_claims(tmp_path, monkeypatch):
    calls = []

    def denied(root):
        calls.append(root)
        raise PermissionError("fixture layout denied")

    monkeypatch.setattr("corki.memory.git_baseline.ensure_layout", denied)

    async def scenario():
        database = tmp_path / "history.db"
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, memories_enabled=True
            ),
            database_path=database,
            home_path=tmp_path / "home",
            model=MainModel(),
            memory_model=MemoryModel(),
        )
        try:
            assert not calls  # Constructor must not fail or write memory layout.
            events = [event async for event in runtime.stream("foreground")]
            assert isinstance(events[-1], TurnCompleted)
            report = await runtime._memory_service.wait()
            assert report.failed == 1 and report.claimed == 0
            assert len(calls) == 1
            assert any(
                "fixture layout denied" in warning for warning in runtime._memory_service.warnings
            )
            with sqlite3.connect(database) as db:
                assert db.execute("SELECT COUNT(*) FROM memory_jobs").fetchone() == (0,)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "variant",
    [
        "guardian",
        "internal_memory",
        "review",
        "compact",
        "memory_consolidation",
        "other",
        "thread_spawn",
    ],
)
def test_typed_non_root_host_skips_background_startup(tmp_path, variant):
    from corki.protocol.session_source import SessionSource, SubAgentSource, ThreadSpawnSource

    if variant in {"guardian", "internal_memory"}:
        source = SessionSource.internal(
            "guardian" if variant == "guardian" else "memory_consolidation"
        )
    else:
        value = "custom child" if variant == "other" else None
        if variant == "thread_spawn":
            value = ThreadSpawnSource(new_thread_id(), 1)
        source = SessionSource.subagent(SubAgentSource(variant, value))

    async def scenario():
        memory_model = MemoryModel()
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, memories_enabled=True
            ),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
            session_source=source,
            model=MainModel(),
            memory_model=memory_model,
        )
        try:
            events = [event async for event in runtime.stream("child work")]
            assert isinstance(events[-1], TurnCompleted)
            assert await runtime._memory_service.wait() is None
            assert not memory_model.extractions
            assert not (tmp_path / "home" / "memories").exists()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("source_name", ["exec", "mcp", "internal_guardian", "subagent_review"])
def test_root_startup_is_not_limited_to_interactive_historical_sources(tmp_path, source_name):
    from corki.protocol.session_source import SessionSource

    async def scenario():
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, memories_enabled=True
            ),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
            thread_id=new_thread_id(),
            session_source=SessionSource.from_startup_arg(source_name),
            model=MainModel(),
            memory_model=MemoryModel(),
        )
        try:
            events = [event async for event in runtime.stream("foreground")]
            assert isinstance(events[-1], TurnCompleted)
            report = await runtime._memory_service.wait()
            assert report is not None
            assert not report.failed, runtime._memory_service.warnings
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("original_internal", [False, True])
@pytest.mark.parametrize("current_internal", [False, True])
def test_runtime_reopen_preserves_history_source_but_uses_current_startup_host(
    tmp_path, original_internal, current_internal
):
    from corki.protocol.session_source import SessionSource
    from corki.storage import SQLiteSessionRepository

    async def scenario():
        database = tmp_path / "history.db"
        old_source = (
            SessionSource.internal("guardian")
            if original_internal
            else SessionSource.from_startup_arg("cli")
        )
        new_source = (
            SessionSource.internal("guardian")
            if current_internal
            else SessionSource.from_startup_arg("exec")
        )
        first = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=database,
            home_path=tmp_path / "home",
            session_source=old_source,
            model=MainModel(),
        )
        try:
            assert isinstance(
                [event async for event in first.stream("original")][-1], TurnCompleted
            )
            original = await first._repository.load_items(first.thread_id)
        finally:
            await first.aclose()
        sessions = SQLiteSessionRepository(database)
        other = new_thread_id()
        await sessions.create_thread(other, tmp_path)
        await sessions.append_items(other, (UserMessageItem("other source", new_turn_id()),))
        memory_model = MemoryModel()
        resumed = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_min_thread_idle_hours=0,
            ),
            database_path=database,
            home_path=tmp_path / "home",
            thread_id=first.thread_id,
            session_source=new_source,
            model=MainModel(),
            memory_model=memory_model,
        )
        try:
            assert isinstance(
                [event async for event in resumed.stream("resumed")][-1], TurnCompleted
            )
            assert await sessions.load_thread_source(first.thread_id) == old_source
            history = await sessions.load_items(first.thread_id)
            assert all(item in history for item in original)
            report = await resumed._memory_service.wait()
            if current_internal:
                assert report is None and not memory_model.extractions
            else:
                assert report.claimed == 1 and report.failed == 0
                assert len(memory_model.extractions) == 1
        finally:
            await resumed.aclose()
            await sessions.close()

    asyncio.run(scenario())


def test_shutdown_joins_cancelled_layout_worker_before_closing_storage(tmp_path, monkeypatch):
    async def scenario():
        entered, cancelled, repository_closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        release, finished = threading.Event(), threading.Event()
        loop = asyncio.get_running_loop()

        def layout(root):
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(5):
                raise TimeoutError("fixture did not release layout worker")
            finished.set()

        monkeypatch.setattr("corki.memory.git_baseline.ensure_layout", layout)
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, memories_enabled=True
            ),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
            model=MainModel(),
            memory_model=MemoryModel(),
        )
        repository = runtime._memory_repository
        close = repository.close

        async def observed_close():
            assert finished.is_set()
            repository_closed.set()
            await close()

        monkeypatch.setattr(repository, "close", observed_close)
        service = runtime._memory_service
        run_once = service.run_once

        async def observed_run(thread, *, parent_permissions):
            try:
                return await run_once(thread, parent_permissions=parent_permissions)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        monkeypatch.setattr(service, "run_once", observed_run)
        closing = None
        try:
            assert isinstance(
                [event async for event in runtime.stream("foreground")][-1], TurnCompleted
            )
            await asyncio.wait_for(entered.wait(), 2)
            worker = service._task
            closing = asyncio.create_task(runtime.aclose())

            # Observe the actual cancellation request, not a fixed sleep guess.
            async def cancellation_requested():
                while not worker.cancelling():
                    await asyncio.sleep(0)

            await asyncio.wait_for(cancellation_requested(), 2)
            assert not closing.done() and not worker.done()
            assert not repository_closed.is_set() and not cancelled.is_set()
            worker.cancel()  # Repeated cancellation still cannot orphan file I/O.
            release.set()
            await asyncio.wait_for(closing, 2)
            assert finished.is_set() and repository_closed.is_set() and cancelled.is_set()
            assert worker.cancelled()
            with sqlite3.connect(tmp_path / "history.db") as db:
                assert db.execute("SELECT COUNT(*) FROM memory_jobs").fetchone() == (0,)
        finally:
            release.set()
            await runtime.aclose()
            if closing is not None:
                await closing

    asyncio.run(scenario())
