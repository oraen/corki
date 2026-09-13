"""Memory startup follows fresh input Turns; all accepted passes remain owned."""

import asyncio
import json
import sqlite3

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory.pipeline import LongTermMemoryService, MemoryRunReport
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.ids import new_turn_id
from corki.protocol.items import AssistantMessageItem, new_step_id
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


class MainModel:
    async def stream(self, request):
        yield ModelCompleted(
            (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
        )

    async def aclose(self):
        pass


def make_runtime(tmp_path, *, memory_model=None, **settings):
    return LangGraphRuntime.create(
        settings=CorkiSettings(
            working_directory=tmp_path,
            api_base="https://example.test/v1",
            skills_enabled=False,
            memories_enabled=True,
            **settings,
        ),
        database_path=tmp_path / "sessions.db",
        home_path=tmp_path / "home",
        memory_root=tmp_path / "memories",
        registry=ToolRegistry(),
        model=MainModel(),
        memory_model=memory_model,
    )


async def complete_turn(runtime, *, realtime=False):
    events = [e async for e in runtime.stream("fresh input", realtime=realtime)]
    assert isinstance(events[-1], TurnCompleted), events[-1]


@pytest.mark.parametrize("generate", [False, True])
@pytest.mark.parametrize("realtime", [False, True])
def test_startup_runs_for_each_new_turn_not_initialization(
    tmp_path, monkeypatch, generate, realtime
):
    async def scenario():
        calls = []

        async def counted(self, thread_id, *, parent_permissions):
            calls.append(thread_id)
            return MemoryRunReport(claimed=len(calls))

        monkeypatch.setattr(LongTermMemoryService, "run_once", counted)
        runtime = make_runtime(tmp_path, memories_generate=generate)
        try:
            await runtime._ensure_ready()
            assert await runtime._memory_service.wait() is None
            assert calls == []
            for count in (1, 2):
                await complete_turn(runtime, realtime=realtime)
                result = await runtime._memory_service.wait()
                assert result.claimed == count
                assert calls == [runtime.thread_id] * count
            with sqlite3.connect(tmp_path / "sessions.db") as db:
                assert db.execute(
                    "SELECT memory_mode FROM threads WHERE id=?", (runtime.thread_id,)
                ).fetchone() == ("enabled" if generate else "disabled",)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["compact", "resume", "failed_admission"])
def test_no_startup_without_a_new_admitted_input_turn(tmp_path, monkeypatch, operation):
    async def scenario():
        calls = []

        async def counted(self, thread_id, *, parent_permissions):
            calls.append(thread_id)
            return MemoryRunReport()

        monkeypatch.setattr(LongTermMemoryService, "run_once", counted)
        runtime = make_runtime(tmp_path)
        try:
            await runtime._ensure_ready()
            save_turn = runtime._repository.save_turn
            if operation == "compact":
                events = [e async for e in runtime.compact()]
            elif operation == "resume":
                await runtime._repository.save_turn(
                    TurnRecord(
                        new_turn_id(), runtime.thread_id, TurnStatus.RUNNING, "pending input"
                    )
                )
                events = [e async for e in runtime.resume_pending()]
            else:

                async def fail_start(record):
                    raise OSError("initial write failed")

                monkeypatch.setattr(runtime._repository, "save_turn", fail_start)
                events = [e async for e in runtime.stream("not durably admitted")]
            assert isinstance(
                events[-1], TurnFailed if operation == "failed_admission" else TurnCompleted
            ), events[-1]
            assert await runtime._memory_service.wait() is None
            assert calls == []
            if operation == "failed_admission":
                monkeypatch.setattr(runtime._repository, "save_turn", save_turn)
            await complete_turn(runtime)
            assert await runtime._memory_service.wait() is not None
            assert calls == [runtime.thread_id]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_background_failure_does_not_disable_the_next_turn_pass(tmp_path, monkeypatch):
    async def scenario():
        calls = []

        async def sometimes_fails(self, thread_id, *, parent_permissions):
            calls.append(thread_id)
            if len(calls) == 1:
                raise OSError("first background pass failed")
            return MemoryRunReport(claimed=2)

        monkeypatch.setattr(LongTermMemoryService, "run_once", sometimes_fails)
        runtime = make_runtime(tmp_path)
        try:
            await complete_turn(runtime)
            with pytest.raises(OSError, match="first background"):
                await runtime._memory_service.wait()
            await complete_turn(runtime)
            assert (await runtime._memory_service.wait()).claimed == 2
            assert len(calls) == 2
            assert any(
                "first background pass failed" in warning
                for warning in runtime._memory_service.warnings
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_steering_does_not_schedule_a_second_background_pass(tmp_path, monkeypatch):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def counted(self, thread_id, *, parent_permissions):
            calls.append(thread_id)
            return MemoryRunReport()

        class HeldMain(MainModel):
            async def stream(self, request):
                entered.set()
                await release.wait()
                async for event in super().stream(request):
                    yield event

        monkeypatch.setattr(LongTermMemoryService, "run_once", counted)
        runtime = make_runtime(tmp_path)
        runtime._graph._model = HeldMain()
        running = asyncio.create_task(complete_turn(runtime, realtime=True))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await runtime.steer("additional instruction within this Turn")
            release.set()
            await running
            await runtime._memory_service.wait()
            assert calls == [runtime.thread_id]
        finally:
            release.set()
            await asyncio.gather(running, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_wait_covers_older_active_pass_and_does_not_cancel_it(tmp_path, monkeypatch):
    async def scenario():
        started, release, exited = asyncio.Event(), asyncio.Event(), asyncio.Event()
        count = 0

        async def overlapping(self, thread_id, *, parent_permissions):
            nonlocal count
            count += 1
            identity = count
            if identity == 1:
                started.set()
                try:
                    await release.wait()
                finally:
                    exited.set()
            return MemoryRunReport(claimed=identity)

        monkeypatch.setattr(LongTermMemoryService, "run_once", overlapping)
        runtime = make_runtime(tmp_path)
        waiter = None
        try:
            await complete_turn(runtime)
            await asyncio.wait_for(started.wait(), 2)
            await complete_turn(runtime)
            waiter = asyncio.create_task(runtime._memory_service.wait())
            await asyncio.sleep(0)
            assert count == 2 and not waiter.done(), "wait must include the older active pass"
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert not exited.is_set(), "a cancelled observer must not cancel the owned pass"
            release.set()
            assert (await runtime._memory_service.wait()).claimed == 2
            assert exited.is_set()
        finally:
            release.set()
            if waiter is not None:
                await asyncio.gather(waiter, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_close_cancels_and_joins_all_passes_despite_repeated_waiter_cancellation(
    tmp_path, monkeypatch
):
    async def scenario():
        started = [asyncio.Event(), asyncio.Event()]
        cancelling = [asyncio.Event(), asyncio.Event()]
        release = asyncio.Event()
        exited = []
        count = 0

        async def held(self, thread_id, *, parent_permissions):
            nonlocal count
            identity = count
            count += 1
            started[identity].set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelling[identity].set()
                await release.wait()
                exited.append(identity)

        monkeypatch.setattr(LongTermMemoryService, "run_once", held)
        runtime = make_runtime(tmp_path)
        closing = None
        try:
            for index in range(2):
                await complete_turn(runtime)
                await asyncio.wait_for(started[index].wait(), 2)
            closing = asyncio.create_task(runtime.aclose())
            await asyncio.wait_for(asyncio.gather(*(e.wait() for e in cancelling)), 2)
            closing.cancel()
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
            assert exited == []
            release.set()
            await runtime.aclose()
            assert sorted(exited) == [0, 1]
        finally:
            release.set()
            if closing is not None:
                await asyncio.gather(closing, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_concurrent_real_passes_share_sqlite_claim_without_losing_worker(tmp_path):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        class MemoryModel:
            def __init__(self):
                self.requests = []

            async def stream(self, request):
                self.requests.append(request)
                started.set()
                await release.wait()
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            json.dumps(
                                {
                                    "memory": "# Memory\nA fact",
                                    "memory_summary": "# v1\nRouting",
                                    "skills": [],
                                }
                            ),
                            request.items[-1].turn_id,
                            new_step_id(),
                        ),
                    )
                )

            async def aclose(self):
                pass

        model = MemoryModel()
        runtime = make_runtime(tmp_path, memory_model=model, memories_generate=False)
        try:
            await complete_turn(runtime)
            await asyncio.wait_for(started.wait(), 2)
            await complete_turn(runtime)
            # Latest pass skips the held global claim; the older worker remains owned.
            latest = await asyncio.wait_for(asyncio.shield(runtime._memory_service._task), 2)
            assert not latest.consolidated
            assert len(model.requests) == 1
            release.set()
            await runtime._memory_service.wait()
            assert (tmp_path / "memories" / "MEMORY.md").read_text() == "v1\n\n# Memory\nA fact\n"
        finally:
            release.set()
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("cleanup_error", [False, True])
def test_real_worker_stream_closes_before_claim_and_repository_release(
    tmp_path, monkeypatch, cleanup_error
):
    async def scenario():
        started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        order = []

        class MemoryModel:
            async def stream(self, request):
                started.set()
                try:
                    await asyncio.Event().wait()
                    yield ModelCompleted(())
                finally:
                    cleaning.set()
                    await release.wait()
                    order.append("stream_closed")
                    if cleanup_error:
                        raise OSError("synthetic memory stream cleanup failure")

            async def aclose(self):
                pass

        runtime = make_runtime(tmp_path, memory_model=MemoryModel(), memories_generate=False)
        closing = None

        def job():
            with sqlite3.connect(tmp_path / "sessions.db") as db:
                return db.execute(
                    "SELECT status, ownership_token FROM memory_jobs "
                    "WHERE kind='memory_consolidate_global'"
                ).fetchone()

        try:
            await complete_turn(runtime)
            await asyncio.wait_for(started.wait(), 5)
            assert job()[0] == "running" and job()[1] is not None
            repository = runtime._memory_repository
            close = repository.close

            async def observed_close():
                order.append("repository_closed")
                await close()

            monkeypatch.setattr(repository, "close", observed_close)
            closing = asyncio.create_task(runtime.aclose())
            await asyncio.wait_for(cleaning.wait(), 5)
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
            assert order == []
            assert job()[0] == "running" and job()[1] is not None
            successor = asyncio.create_task(runtime.aclose())
            await asyncio.sleep(0)
            successor.cancel()
            with pytest.raises(asyncio.CancelledError):
                await successor
            assert order == []
            release.set()
            await asyncio.wait_for(runtime.aclose(), 5)
            assert order == ["stream_closed", "repository_closed"]
            assert job() == ("failed", None)
            assert not (tmp_path / "memories" / "MEMORY.md").exists()
            assert runtime._memory_service.retained_workers == ()
        finally:
            release.set()
            if closing is not None:
                await asyncio.gather(closing, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("generate", [False, True])
def test_explicit_background_pause_is_independent_of_source_eligibility(
    tmp_path, monkeypatch, generate
):
    async def scenario():
        async def unexpected(self, thread_id, *, parent_permissions):
            pytest.fail("background pass should be paused")

        monkeypatch.setattr(LongTermMemoryService, "run_once", unexpected)
        runtime = make_runtime(
            tmp_path, memories_generate=generate, memories_background_enabled=False
        )
        try:
            await complete_turn(runtime)
            assert await runtime._memory_service.wait() is None
            with sqlite3.connect(tmp_path / "sessions.db") as db:
                assert db.execute(
                    "SELECT memory_mode FROM threads WHERE id=?", (runtime.thread_id,)
                ).fetchone() == ("enabled" if generate else "disabled",)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
