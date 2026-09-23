"""Paused real SQLite workers must not outlive their Runtime claim/usage owner."""

import asyncio
import json
import sqlite3
import threading
import time

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory.sqlite import SQLiteMemoryRepository
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize("phase", ["extraction", "consolidation"])
@pytest.mark.parametrize("cancel_close_waiter", [False, True])
@pytest.mark.parametrize(
    "pause",
    [
        "before_commit",
        "after_commit",
        "new_owner",
        "claim_error",
        "cleanup_error",
        "unknown_commit",
    ],
)
def test_runtime_close_joins_claim_and_releases_only_its_undispatched_lease(
    tmp_path, monkeypatch, phase, pause, cancel_close_waiter
):
    async def scenario():
        database = tmp_path / "s.db"
        sessions = SQLiteSessionRepository(database)
        source = new_thread_id()
        if phase == "extraction":
            await sessions.create_thread(source, tmp_path)
            await sessions.append_items(
                source, (UserMessageItem("SOURCE_EVIDENCE", new_turn_id()),)
            )
        method = "_claim_extraction_jobs" if phase == "extraction" else "_claim_consolidation"
        original = getattr(SQLiteMemoryRepository, method)
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        claimed, memory_requests = [], []

        def paused(repository, *args):
            if pause in ("before_commit", "claim_error"):
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(5), "fixture claim barrier not released"
                if pause == "claim_error":
                    raise RuntimeError("claim database fixture failure")
            result = original(repository, *args)
            claimed.append(result)
            if pause not in ("before_commit", "claim_error"):
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(5), "fixture claim barrier not released"
                if pause == "unknown_commit":
                    raise RuntimeError("claim response lost after commit")
            return result

        monkeypatch.setattr(SQLiteMemoryRepository, method, paused)
        if pause == "cleanup_error":

            def fail_cleanup(*args):
                raise RuntimeError("failure recording database fixture error")

            monkeypatch.setattr(SQLiteMemoryRepository, "_fail_job", fail_cleanup)

        class Main:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("ready", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Memory(Main):
            async def stream(self, request):
                memory_requests.append(request)
                raise AssertionError("cancelled undispatched claim must not sample")
                yield

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_min_thread_idle_hours=0,
                memories_retry_delay_seconds=17,
            ),
            database_path=database,
            home_path=tmp_path / "home",
            memory_root=tmp_path / "memory",
            model=Main(),
            memory_model=Memory(),
        )
        close = None
        try:
            assert isinstance([e async for e in runtime.stream("work")][-1], TurnCompleted)
            await asyncio.wait_for(entered.wait(), 3)
            key = str(source) if phase == "extraction" else "global"
            if pause == "new_owner":
                with sqlite3.connect(database) as db:
                    db.execute(
                        "UPDATE memory_jobs SET ownership_token='new-owner' WHERE job_key=?", (key,)
                    )
            earliest_failure = int(time.time())
            close = asyncio.create_task(runtime.aclose())
            await asyncio.sleep(0.05)
            closed_early = close.done()
            if cancel_close_waiter:
                close.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await close
                close = asyncio.create_task(runtime.aclose())
                await asyncio.sleep(0)
                closed_early |= close.done()
            release.set()
            await asyncio.wait_for(asyncio.shield(close), 3)
            with sqlite3.connect(database) as db:
                row = db.execute(
                    "SELECT status, ownership_token FROM memory_jobs WHERE job_key=?", (key,)
                ).fetchone()
            if pause in ("cleanup_error", "unknown_commit"):
                owned = claimed[0][0] if phase == "extraction" else claimed[0]
                expected = ("running", owned.ownership_token)
            elif pause == "claim_error":
                expected = None
            else:
                expected = ("running", "new-owner") if pause == "new_owner" else ("failed", None)
            assert (closed_early, row, len(memory_requests), len(claimed)) == (
                False,
                expected,
                0,
                0 if pause == "claim_error" else 1,
            )
            if pause in ("claim_error", "cleanup_error", "unknown_commit"):
                assert runtime._memory_service._warnings
            elif pause != "new_owner":
                with sqlite3.connect(database) as db:
                    retries, retry_at = db.execute(
                        "SELECT retry_remaining, retry_at FROM memory_jobs WHERE job_key=?", (key,)
                    ).fetchone()
                assert retries == 2
                assert earliest_failure + 17 <= retry_at <= int(time.time()) + 17
        finally:
            release.set()
            if close is not None:
                await close
            await runtime.aclose()
            await sessions.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("one_completed", [False, True])
def test_runtime_close_releases_claims_waiting_for_extraction_concurrency(tmp_path, one_completed):
    async def scenario():
        database = tmp_path / "s.db"
        sessions = SQLiteSessionRepository(database)
        for index in range(9):
            source = new_thread_id()
            await sessions.create_thread(source, tmp_path)
            await sessions.append_items(
                source, (UserMessageItem(f"SOURCE_{index}", new_turn_id()),)
            )
        entered = asyncio.Event()
        requests = []

        class Main:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("ready", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Memory(Main):
            async def stream(self, request):
                requests.append(request)
                if one_completed and len(requests) == 1:
                    value = {
                        "raw_memory": "completed fact",
                        "rollout_summary": "summary",
                        "rollout_slug": None,
                    }
                    yield ModelCompleted(
                        (
                            AssistantMessageItem(
                                json.dumps(value), request.items[-1].turn_id, new_step_id()
                            ),
                        )
                    )
                    return
                if len(requests) == (9 if one_completed else 8):
                    entered.set()
                await asyncio.Event().wait()
                yield

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_min_thread_idle_hours=0,
                memories_max_threads_per_startup=9,
            ),
            database_path=database,
            home_path=tmp_path / "home",
            memory_root=tmp_path / "memory",
            model=Main(),
            memory_model=Memory(),
        )
        try:
            assert isinstance([e async for e in runtime.stream("work")][-1], TurnCompleted)
            await asyncio.wait_for(entered.wait(), 5)
            await runtime.aclose()
            with sqlite3.connect(database) as db:
                rows = db.execute(
                    "SELECT status, ownership_token, retry_remaining FROM memory_jobs "
                    "WHERE kind='memory_stage1'"
                ).fetchall()
            assert len(rows) == 9
            assert rows.count(("failed", None, 2)) == 9 - int(one_completed)
            assert rows.count(("succeeded", None, 3)) == int(one_completed)
            assert len(requests) == (9 if one_completed else 8)
        finally:
            await runtime.aclose()
            await sessions.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("worker_fails", [False, True])
def test_foreground_citation_write_is_joined_before_runtime_close(
    tmp_path, monkeypatch, worker_fails
):
    async def scenario():
        database = tmp_path / "s.db"
        sessions = SQLiteSessionRepository(database)
        source = new_thread_id()
        await sessions.create_thread(source, tmp_path)
        repository = SQLiteMemoryRepository(database)
        with repository._connect() as db:
            db.execute(
                "INSERT INTO memory_stage1_outputs(thread_id, source_updated_at, cwd, "
                "raw_memory, rollout_summary) "
                "VALUES (?, CURRENT_TIMESTAMP, ?, 'fact', 'summary')",
                (str(source), str(tmp_path)),
            )
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        original = repository._mark_memories_used
        calls = []

        def paused(ids):
            calls.append(ids)
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5), "fixture usage barrier not released"
            if worker_fails:
                raise RuntimeError("usage worker fixture failure")
            original(ids)

        monkeypatch.setattr(repository, "_mark_memories_used", paused)

        class Model:
            async def stream(self, request):
                text = (
                    f"answer<oai-mem-citation><rollout_ids>{source}"
                    "</rollout_ids></oai-mem-citation>"
                )
                yield ModelCompleted(
                    (AssistantMessageItem(text, request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=database,
            memory_repository=repository,
            model=Model(),
            home_path=tmp_path / "home",
        )

        async def consume():
            return [e async for e in runtime.stream("cite memory")]

        consumer = asyncio.create_task(consume())
        close = None
        try:
            await asyncio.wait_for(entered.wait(), 3)
            close = asyncio.create_task(runtime.aclose())
            await asyncio.sleep(0.05)
            closed_early = close.done()
            release.set()
            await asyncio.wait_for(asyncio.shield(close), 3)
            with pytest.raises(asyncio.CancelledError):
                await consumer
            with repository._connect() as db:
                used = db.execute(
                    "SELECT usage_count FROM memory_stage1_outputs WHERE thread_id=?",
                    (str(source),),
                ).fetchone()[0]
            assert (closed_early, used, len(calls)) == (False, int(not worker_fails), 1)
        finally:
            release.set()
            if close is not None:
                await close
            await runtime.aclose()
            await asyncio.gather(consumer, return_exceptions=True)
            await sessions.close()

    asyncio.run(scenario())
