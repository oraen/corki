import asyncio
from contextlib import aclosing

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import ContextCompacted, TurnCancelled, TurnCompleted
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    TurnAbortedItem,
    UserMessageItem,
    new_step_id,
)
from corki.tools import ToolRegistry


@pytest.mark.parametrize("close", [False, True])
def test_manual_compaction_cancel_or_close_preserves_history_and_cleans_up(tmp_path, close):
    async def scenario():
        entered, cleaned = asyncio.Event(), asyncio.Event()

        class Model:
            async def stream(self, request):
                entered.set()
                try:
                    await asyncio.Event().wait()
                    yield
                finally:
                    cleaned.set()

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, event_queue_size=1
            ),
            database_path=tmp_path / "sessions.db",
            model=Model(),
            registry=ToolRegistry(),
        )
        events = []

        async def consume_recorded():
            try:
                async with aclosing(runtime.compact()) as stream:
                    async for event in stream:
                        events.append(event)
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(consume_recorded())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            before = await runtime._repository.load_items(runtime.thread_id)
            await (runtime.aclose() if close else runtime.cancel_active())
            await asyncio.wait_for(task, 3)
            assert cleaned.is_set()
            assert isinstance(events[-1], TurnCancelled)
            assert not any(isinstance(e, ContextCompacted) for e in events)
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert tuple(i for i in stored if not isinstance(i, TurnAbortedItem)) == before == ()
            assert len(stored) == 1 and isinstance(stored[0], TurnAbortedItem)
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_close_with_unconsumed_compaction_events_preserves_atomic_commit(tmp_path):
    async def scenario():
        committed = asyncio.Event()

        class Model:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("SUMMARY", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, event_queue_size=1
            ),
            database_path=tmp_path / "sessions.db",
            model=Model(),
            registry=ToolRegistry(),
        )
        append = runtime._repository.append_items

        async def observe(thread, items):
            await append(thread, items)
            if any(isinstance(i, CompactionItem) for i in items):
                committed.set()

        runtime._repository.append_items = observe
        stream = runtime.compact()
        try:
            await anext(stream)  # Started only; queue1 then holds CompactionStarted.
            await asyncio.wait_for(committed.wait(), 3)
            await asyncio.wait_for(runtime.aclose(), 3)
            await asyncio.wait_for(stream.aclose(), 3)
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert len(stored) == 2 and isinstance(stored[0], CompactionItem)
            assert isinstance(stored[1], TurnAbortedItem)
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
        finally:
            await stream.aclose()
            await runtime.aclose()

    asyncio.run(scenario())


def test_manual_compaction_replaces_a_running_normal_turn(tmp_path):
    async def scenario():
        entered = asyncio.Event()
        requests, cancelled = [], []

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    entered.set()
                    await asyncio.Event().wait()
                yield ModelCompleted(
                    (AssistantMessageItem("SUMMARY", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, event_queue_size=1
            ),
            database_path=tmp_path / "sessions.db",
            model=Model(),
            registry=ToolRegistry(),
        )

        async def old_turn():
            try:
                async for event in runtime.stream("active user request"):
                    cancelled.append(event)
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(old_turn())
        try:
            await asyncio.wait_for(entered.wait(), 3)

            async def compact():
                return [e async for e in runtime.compact()]

            events = await asyncio.wait_for(compact(), 3)
            await asyncio.wait_for(task, 3)
            assert isinstance(cancelled[-1], TurnCancelled)
            assert isinstance(events[-1], TurnCompleted)
            assert len(requests) == 2 and requests[-1].tools == ()
            assert any(
                isinstance(i, UserMessageItem) and i.content == "active user request"
                for i in requests[-1].items
            )
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())
