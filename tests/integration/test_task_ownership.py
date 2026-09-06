"""Fault windows mapped to Codex task abort / parallel tool ownership.

Check before asyncio.run's shutdown: its global cancellation can hide leaks.
Scripted completions establish harness behavior, not real model choice quality.
"""

import asyncio
from pathlib import Path

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelRequest, ModelTextDelta
from corki.protocol.events import TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.ids import ToolCallId
from corki.protocol.items import AssistantMessageItem, ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall, ToolConcurrency, ToolResult, ToolSpec
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolContext, ToolRegistry


class OwnedIterator:
    def __init__(self, request: ModelRequest, *, complete: bool) -> None:
        self.request = request
        self.complete = complete
        self.started = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.closed = False
        self.advanced = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        self.task = asyncio.current_task()
        self.started.set()
        if self.complete:
            if self.advanced:
                raise AssertionError("read past authoritative ModelCompleted")
            self.advanced = True
            return ModelCompleted(
                (AssistantMessageItem("done", self.request.items[-1].turn_id, new_step_id()),)
            )
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        self.closed = True


class OwnedModel:
    def __init__(self, *, complete: bool) -> None:
        self.complete = complete
        self.created = asyncio.Event()
        self.iterator: OwnedIterator | None = None

    def stream(self, request: ModelRequest):
        self.iterator = OwnedIterator(request, complete=self.complete)
        self.created.set()
        return self.iterator

    async def aclose(self) -> None:
        # Closing the shared model client is not ownership of one response.
        pass


def test_realtime_parent_cancellation_closes_response_and_both_waiters(tmp_path: Path) -> None:
    async def scenario() -> None:
        model = OwnedModel(complete=False)
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path),
            database_path=tmp_path / "cancel.db",
            model=model,
        )
        command_started = asyncio.Event()
        command_tasks: list[asyncio.Task] = []
        original_next = runtime._realtime.next

        async def tracked_next():
            command_tasks.append(asyncio.current_task())
            command_started.set()
            return await original_next()

        runtime._realtime.next = tracked_next
        events = []

        async def consume() -> None:
            async for event in runtime.stream("wait", realtime=True):
                events.append(event)

        consumer = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(model.created.wait(), timeout=3)
            assert model.iterator is not None
            await asyncio.wait_for(model.iterator.started.wait(), timeout=3)
            await asyncio.wait_for(command_started.wait(), timeout=3)
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(consumer, timeout=3)
            assert model.iterator.closed, "response iterator leaked after turn cancellation"
            assert model.iterator.task.done(), "model read survived its parent"
            assert all(task.done() for task in command_tasks), "steering reader survived its parent"
            assert sum(isinstance(event, TurnCancelled) for event in events) == 1
            assert not any(isinstance(event, (TurnCompleted, TurnFailed)) for event in events)
        finally:
            tasks = [consumer, *command_tasks]
            if model.iterator is not None and model.iterator.task is not None:
                tasks.append(model.iterator.task)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("realtime", [False, True])
def test_model_completion_closes_response_without_reading_tail(
    tmp_path: Path, realtime: bool
) -> None:
    async def scenario() -> None:
        model = OwnedModel(complete=True)
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path),
            database_path=tmp_path / "completed.db",
            model=model,
        )
        try:
            events = [event async for event in runtime.stream("finish", realtime=realtime)]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert events[-1].final_answer == "done"
            assert model.iterator.closed, "completed response was not released"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("realtime", [False, True])
def test_closing_consumer_joins_graph_and_response_under_backpressure(
    tmp_path: Path, realtime: bool
) -> None:
    class BurstModel:
        def __init__(self) -> None:
            self.third_delta = asyncio.Event()
            self.closed_response = False

        async def stream(self, request: ModelRequest):
            try:
                for index in range(10):
                    if index == 2:
                        self.third_delta.set()
                    yield ModelTextDelta("partial")
            finally:
                self.closed_response = True

        async def aclose(self) -> None:
            pass

    async def scenario() -> None:
        model = BurstModel()
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, event_queue_size=1),
            database_path=tmp_path / "backpressure.db",
            model=model,
        )
        stream = runtime.stream("burst", realtime=realtime)
        try:
            await anext(stream)  # TurnStarted
            await anext(stream)  # Consume one delta; subsequent events fill the queue.
            await asyncio.wait_for(model.third_delta.wait(), timeout=3)
            await asyncio.wait_for(stream.aclose(), timeout=3)
            assert model.closed_response, "closing consumer did not close the running response"
            assert not runtime._realtime.active
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
            assert not any(
                task.get_name().startswith("corki-turn-") and not task.done()
                for task in asyncio.all_tasks()
            )
        finally:
            await stream.aclose()
            # On the broken implementation the nested generator is finalized
            # later; let it clean up before closing the test's SQLite handle.
            await asyncio.sleep(0)
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("realtime", [False, True])
def test_closing_at_turn_started_finalizes_durable_turn(tmp_path: Path, realtime: bool) -> None:
    async def scenario() -> None:
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path),
            database_path=tmp_path / "start-close.db",
            model=OwnedModel(complete=False),
        )
        stream = runtime.stream("close immediately", realtime=realtime)
        try:
            await anext(stream)
            await asyncio.wait_for(stream.aclose(), timeout=3)
            assert not runtime._realtime.active, "realtime activation survived closed turn"
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
        finally:
            await stream.aclose()
            await runtime.aclose()

    asyncio.run(scenario())


class BlockingTool:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False
        self.task: asyncio.Task | None = None
        self.calls = 0
        self.spec = ToolSpec(
            "block", "blocking tool", {"type": "object"}, concurrency=ToolConcurrency.PARALLEL
        )

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        self.calls += 1
        self.task = asyncio.current_task()
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("unreachable")


class FastTool:
    spec = ToolSpec("fast", "fast tool", {"type": "object"}, concurrency=ToolConcurrency.PARALLEL)

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        return ToolResult(call.id, call.name, "done")


class ParallelModel:
    async def stream(self, request: ModelRequest):
        step = new_step_id()
        yield ModelCompleted(
            tuple(
                ToolCallItem(ToolCall(ToolCallId(name), name, {}), request.items[-1].turn_id, step)
                for name in ("fast", "block")
            )
        )

    async def aclose(self) -> None:
        pass


@pytest.mark.parametrize("fault", ["claim", "complete"])
def test_parallel_storage_failure_joins_siblings_before_turn_failure(
    tmp_path: Path, fault: str
) -> None:
    async def scenario() -> None:
        block = BlockingTool()

        class FaultRepository(SQLiteSessionRepository):
            async def claim_tool_call(self, thread_id, turn_id, call):
                if fault == "claim" and call.name == "fast":
                    await block.started.wait()
                    raise OSError("injected claim failure")
                return await super().claim_tool_call(thread_id, turn_id, call)

            async def complete_tool_call(self, thread_id, turn_id, result):
                if fault == "complete" and result.tool_name == "fast":
                    await block.started.wait()
                    raise OSError("injected completion failure")
                await super().complete_tool_call(thread_id, turn_id, result)

        registry = ToolRegistry()
        registry.register(FastTool())
        registry.register(block)
        repository = FaultRepository(tmp_path / "parallel.db")
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path),
            database_path=repository.path,
            model=ParallelModel(),
            repository=repository,
            registry=registry,
        )
        try:

            async def consume():
                return [event async for event in runtime.stream("both")]

            events = await asyncio.wait_for(consume(), timeout=3)
            assert isinstance(events[-1], TurnFailed)
            assert "injected" in events[-1].error
            assert block.cancelled, "sibling tool still executing after TurnFailed"
            assert block.task.done(), "sibling task was not joined"
            assert sum(isinstance(event, TurnFailed) for event in events) == 1
            replay = await repository.claim_tool_call(
                runtime.thread_id, events[-1].turn_id, ToolCall(ToolCallId("block"), "block", {})
            )
            assert replay.is_error and "unknown" in replay.content
            assert block.calls == 1
        finally:
            if block.task is not None:
                block.task.cancel()
                await asyncio.gather(block.task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())
