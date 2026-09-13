"""The first durable Turn write belongs to a worker before cancellation is possible."""

import asyncio
import threading
from contextlib import suppress

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import (
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
    TurnStarted,
    WarningEvent,
)
from corki.protocol.items import UserMessageItem
from corki.sessions import TurnStatus
from corki.tools import ToolRegistry


class RecordingModel:
    def __init__(self):
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        yield ModelCompleted(())

    async def aclose(self):
        pass


def create_runtime(tmp_path, model, thread_id=None):
    return LangGraphRuntime.create(
        settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
        database_path=tmp_path / "sessions.db",
        registry=ToolRegistry(),
        model=model,
        thread_id=thread_id,
    )


@pytest.mark.parametrize("mode", ["normal", "realtime", "compact"])
@pytest.mark.parametrize("boundary", ["before_commit", "after_commit"])
@pytest.mark.parametrize("action", ["stop", "consumer", "close"])
def test_cancel_first_write_is_owned_joined_and_not_resumed(
    tmp_path, monkeypatch, mode, boundary, action
):
    async def scenario():
        entered, release = threading.Event(), threading.Event()
        model = RecordingModel()
        runtime = create_runtime(tmp_path, model)
        original = runtime._repository._save_turn
        writes, events = [], []

        def save(record):
            if record.status is TurnStatus.RUNNING:
                if boundary == "after_commit":
                    original(record)
                entered.set()
                assert release.wait(5)
                if boundary == "before_commit":
                    original(record)
            else:
                original(record)
            writes.append(record.status)

        monkeypatch.setattr(runtime._repository, "_save_turn", save)

        async def consume():
            stream = (
                runtime.compact()
                if mode == "compact"
                else runtime.stream("accepted", realtime=mode == "realtime")
            )
            with suppress(asyncio.CancelledError):
                async for event in stream:
                    events.append(event)

        consumer = asyncio.create_task(consume())
        closing = None
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            assert isinstance(events[0], TurnStarted)
            # Admission can warn before the first durable write, but cannot
            # emit model/tool data or a terminal while that write is held.
            assert all(isinstance(event, WarningEvent) for event in events[1:])
            assert runtime._active_run is not None and not runtime._active_run.done.is_set()
            if action == "stop":
                await runtime.cancel_active()
                await runtime.cancel_active()
            elif action == "consumer":
                consumer.cancel()
                for _ in range(4):
                    await asyncio.sleep(0)
                consumer.cancel()
            else:
                closing = asyncio.create_task(runtime.aclose())
            for _ in range(8):
                await asyncio.sleep(0)
            assert not consumer.done()
            assert closing is None or not closing.done()
            assert not runtime._active_run.done.is_set() and not model.requests
            release.set()
            await asyncio.wait_for(consumer, 3)
            if closing is not None:
                await asyncio.wait_for(closing, 3)
            assert isinstance(events[-1], TurnCancelled), events
            assert (
                sum(isinstance(e, (TurnCompleted, TurnFailed, TurnCancelled)) for e in events) == 1
            )
            assert writes == [TurnStatus.RUNNING, TurnStatus.CANCELLED]
            assert not runtime._realtime.active and not model.requests
            thread_id = runtime.thread_id
            await runtime.aclose()

            resumed = create_runtime(tmp_path, model, thread_id)
            try:
                assert [event async for event in resumed.resume_pending()] == []
                history = await resumed._repository.load_items(thread_id)
                assert [i.content for i in history if isinstance(i, UserMessageItem)] == (
                    [] if mode == "compact" else ["accepted"]
                )
                following = [event async for event in resumed.stream("next")]
                assert isinstance(following[-1], TurnCompleted)
                assert len(model.requests) == 1
            finally:
                await resumed.aclose()
        finally:
            release.set()
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            if closing is not None:
                await asyncio.gather(closing, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("committed", [False, True])
def test_write_error_after_cancel_is_logged_without_replacing_cancel_terminal(
    tmp_path, monkeypatch, caplog, committed
):
    async def scenario():
        entered, release = threading.Event(), threading.Event()
        model = RecordingModel()
        runtime = create_runtime(tmp_path, model)
        original = runtime._repository._save_turn
        events = []

        def save(record):
            if record.status is TurnStatus.RUNNING:
                entered.set()
                assert release.wait(5)
                if committed:
                    original(record)
                raise OSError("write fault after cancellation")
            original(record)

        monkeypatch.setattr(runtime._repository, "_save_turn", save)

        async def consume():
            with suppress(asyncio.CancelledError):
                async for event in runtime.stream("accepted"):
                    events.append(event)

        consumer = asyncio.create_task(consume())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            await runtime.cancel_active()
            await asyncio.sleep(0)
            assert not consumer.done()
            release.set()
            await asyncio.wait_for(consumer, 3)
            assert isinstance(events[-1], TurnCancelled)
            assert not any(isinstance(event, TurnFailed) for event in events)
            assert not model.requests
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
            assert "write fault after cancellation" in caplog.text
        finally:
            release.set()
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("compact", [False, True])
@pytest.mark.parametrize("committed", [False, True])
def test_first_turn_write_failure_uses_storage_terminal_without_sampling(
    tmp_path, monkeypatch, compact, committed
):
    async def scenario():
        model = RecordingModel()
        runtime = create_runtime(tmp_path, model)
        original = runtime._repository._save_turn

        def fail(record):
            if record.status is TurnStatus.RUNNING:
                if committed:
                    original(record)
                raise OSError("initial Turn write fault")
            original(record)

        monkeypatch.setattr(runtime._repository, "_save_turn", fail)
        try:
            stream = runtime.compact() if compact else runtime.stream("accepted")
            events = [event async for event in stream]
            assert isinstance(events[0], TurnStarted)
            assert isinstance(events[-1], TurnFailed)
            assert events[-1].error_kind == "storage"
            assert "initial Turn write fault" in events[-1].error
            assert not model.requests
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
            assert [event async for event in runtime.resume_pending()] == []
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
