"""Startup retention and visible consolidation inputs through the actual Runtime."""

import asyncio
import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta

import pytest
from memory_evidence import inspect_worker_evidence

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory import SQLiteMemoryRepository
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_thread_id
from corki.protocol.items import AssistantMessageItem, ContextItem, new_step_id
from corki.storage import SQLiteSessionRepository


def populate(database, workspace):
    SQLiteSessionRepository(database)
    SQLiteMemoryRepository(database)
    now = datetime.now(UTC)
    old = (now - timedelta(days=60)).isoformat()
    fresh = (now - timedelta(days=1)).isoformat()
    entries = [(f"stale-{index:03}", old, 0, "enabled") for index in range(201)]
    entries += [
        ("selected-old", old, 1, "enabled"),
        ("polluted", fresh, 1, "polluted"),
        ("valid", fresh, 0, "enabled"),
    ]
    with sqlite3.connect(database) as connection:
        for thread, source, selected, mode in entries:
            connection.execute(
                "INSERT INTO threads(id,cwd,updated_at,memory_mode) VALUES (?,?,?,?)",
                (thread, str(workspace), source, mode),
            )
            connection.execute(
                "INSERT INTO memory_stage1_outputs(thread_id,cwd,source_updated_at,raw_memory,"
                "rollout_summary,selected_for_phase2) VALUES (?,?,?,?,?,?)",
                (thread, str(workspace), source, f"detail-{thread}", f"summary-{thread}", selected),
            )
            connection.execute(
                "INSERT INTO memory_jobs(kind,job_key,status,source_updated_at,"
                "last_success_source_updated_at) VALUES ('memory_stage1',?,'succeeded',?,?)",
                (thread, source, source),
            )


class MainModel:
    def __init__(self):
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        yield ModelCompleted(
            (AssistantMessageItem("ready", request.items[-1].turn_id, new_step_id()),)
        )

    async def aclose(self):
        pass


class MemoryModel:
    def __init__(self):
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        assert request.tools and request.output_schema is None
        value = inspect_worker_evidence(request)
        assert "detail-valid" in value["raw_memories"]
        assert "detail-stale" not in value["raw_memories"]
        assert "detail-selected-old" not in value["raw_memories"]
        assert "detail-polluted" not in value["raw_memories"]
        yield ModelCompleted(
            (
                AssistantMessageItem(
                    json.dumps(
                        {
                            "memory": "detail-valid",
                            "memory_summary": "Only valid sources indexed.",
                            "skills": [],
                        }
                    ),
                    request.items[-1].turn_id,
                    new_step_id(),
                ),
            )
        )


def test_runtime_prunes_only_one_batch_then_consolidates_visible_inputs(tmp_path):
    async def scenario():
        database, root = tmp_path / "sessions.db", tmp_path / "memories"
        populate(database, tmp_path)
        current = new_thread_id()
        memory = MemoryModel()
        for index in range(2):
            main = MainModel()
            runtime = await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    working_directory=tmp_path, skills_enabled=False, memories_enabled=True
                ),
                database_path=database,
                thread_id=current,
                model=main,
                memory_model=memory,
                memory_root=root,
            )
            try:
                events = [event async for event in runtime.stream("initialize")]
                assert isinstance(events[-1], TurnCompleted)
                report = await runtime._memory_service.wait()
                assert report.claimed == 0 and report.failed == 0
                assert len(memory.requests) == 1
                with sqlite3.connect(database) as connection:
                    names = {
                        row[0]
                        for row in connection.execute("SELECT thread_id FROM memory_stage1_outputs")
                    }
                    assert names == (
                        {"stale-200", "selected-old", "polluted", "valid"}
                        if index == 0
                        else {"polluted", "valid"}
                    )
                    assert (
                        connection.execute(
                            "SELECT COUNT(*) FROM memory_jobs WHERE kind='memory_stage1'"
                        ).fetchone()[0]
                        == 204
                    )
                assert "detail-valid" in (root / "MEMORY.md").read_text()
                assert len(tuple((root / "rollout_summaries").glob("*.md"))) == 1
                await runtime._memory_service.wait()
                prefix = await runtime._repository.load_items(current)
                assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
                events = [event async for event in runtime.stream("recall")]
                assert isinstance(events[-1], TurnCompleted)
                assert (await runtime._repository.load_items(current))[: len(prefix)] == prefix
                latest = next(
                    item
                    for item in reversed(main.requests[-1].items)
                    if isinstance(item, ContextItem) and item.key == "memory.instructions"
                )
                assert "Only valid sources indexed." in latest.content
                assert not runtime._memory_service.warnings
            finally:
                await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["error", "cancel"])
def test_prune_failure_isolated_but_cancellation_is_not_swallowed(tmp_path, failure):
    async def scenario():
        database, root = tmp_path / "sessions.db", tmp_path / "memories"
        populate(database, tmp_path)

        class Repository(SQLiteMemoryRepository):
            prunes = 0

            async def prune_stage_one_outputs(self, *, max_unused_days, limit):
                self.prunes += 1
                if failure == "cancel":
                    raise asyncio.CancelledError
                raise OSError("injected retention failure")

        repository, memory = Repository(database), MemoryModel()
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, memories_enabled=True
            ),
            database_path=database,
            model=MainModel(),
            memory_model=memory,
            memory_repository=repository,
            memory_root=root,
        )
        try:
            events = [event async for event in runtime.stream("normal work")]
            assert isinstance(events[-1], TurnCompleted)
            if failure == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await runtime._memory_service.wait()
                assert not memory.requests and not runtime._memory_service.warnings
            else:
                report = await runtime._memory_service.wait()
                assert report.consolidated and not report.failed
                assert any(
                    "injected retention failure" in warning
                    for warning in runtime._memory_service.warnings
                )
            assert repository.prunes == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_runtime_close_joins_already_started_retention_write(tmp_path):
    async def scenario():
        database = tmp_path / "sessions.db"
        populate(database, tmp_path)
        entered, release, finished = asyncio.Event(), threading.Event(), threading.Event()
        loop = asyncio.get_running_loop()

        class Repository(SQLiteMemoryRepository):
            def _prune_stage_one_outputs(self, max_unused_days, limit):
                loop.call_soon_threadsafe(entered.set)
                try:
                    assert release.wait(timeout=5), "test must release the owned database worker"
                    return super()._prune_stage_one_outputs(max_unused_days, limit)
                finally:
                    finished.set()

        memory = MemoryModel()
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, memories_enabled=True
            ),
            database_path=database,
            model=MainModel(),
            memory_model=memory,
            memory_repository=Repository(database),
            memory_root=tmp_path / "memories",
        )
        closer = None
        try:
            assert isinstance([event async for event in runtime.stream("work")][-1], TurnCompleted)
            await asyncio.wait_for(entered.wait(), 2)
            closer = asyncio.create_task(runtime.aclose())
            async with asyncio.timeout(2):
                while not runtime._memory_service._task.cancelling():
                    await asyncio.sleep(0)
            assert not closer.done() and not finished.is_set()
            release.set()
            await asyncio.wait_for(closer, 2)
            assert finished.is_set()
            assert runtime._memory_service._task.cancelled()
            assert not memory.requests
            with sqlite3.connect(database) as connection:
                assert (
                    connection.execute("SELECT COUNT(*) FROM memory_stage1_outputs").fetchone()[0]
                    == 4
                )
        finally:
            release.set()
            if closer is not None:
                await closer
            await runtime.aclose()

    asyncio.run(scenario())
