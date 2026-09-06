import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest

import corki.core.runtime as runtime_module
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import (
    ModelCompleted,
    ModelError,
    ModelErrorKind,
    ModelRequest,
    ModelTextDelta,
)
from corki.models.types import ModelEvent
from corki.protocol.events import TurnCompleted, TurnFailed, TurnStarted
from corki.protocol.ids import ToolCallId, new_thread_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolConcurrency, ToolResult, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolContext, ToolRegistry


class NeverCalledModel:
    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        del request
        self.calls += 1
        raise AssertionError("durably committed model step was sampled again")
        yield

    async def aclose(self) -> None:
        return None


class BlockingModel:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        del request
        self.started.set()
        await asyncio.Event().wait()
        yield

    async def aclose(self) -> None:
        return None


class AnswerModel:
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield ModelCompleted(
            (
                AssistantMessageItem(
                    "continued from checkpoint",
                    request.items[-1].turn_id,
                    new_step_id(),
                ),
            )
        )

    async def aclose(self) -> None:
        return None


class NullSink:
    async def emit(self, event: object) -> None:
        del event


@dataclass
class FakeNodeRuntime:
    context: GraphRunContext


def test_replayed_completed_tool_result_has_one_durable_history_item(tmp_path: Path) -> None:
    async def scenario() -> tuple[tuple[object, ...], int]:
        registry = ToolRegistry()
        tool = DelayTool("stable", 0)
        registry.register(tool)
        repository = SQLiteSessionRepository(tmp_path / "stable.db")
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path),
            database_path=tmp_path / "stable.db",
            repository=repository,
            registry=registry,
            model=AnswerModel(),
        )
        thread_id, turn_id = runtime.thread_id, new_turn_id()
        await repository.create_thread(thread_id, tmp_path)
        user = UserMessageItem("run once", turn_id)
        state = _initial_state(thread_id, turn_id, runtime._settings, user)
        state["request_tools"] = (tool.spec,)
        call = ToolCall(ToolCallId("stable-call"), "stable", {})
        context = FakeNodeRuntime(GraphRunContext(events=NullSink()))

        first = await runtime._graph._execute_one(state, context, call)
        await repository.append_items(thread_id, (first,))
        second = await runtime._graph._execute_one(state, context, call)
        await repository.append_items(thread_id, (second,))
        stored = await repository.load_items(thread_id)
        await runtime.aclose()
        return stored, tool.calls

    stored, calls = asyncio.run(scenario())

    results = tuple(item for item in stored if isinstance(item, ToolResultItem))
    assert calls == 1
    assert len(results) == 1


def test_resume_uses_atomic_model_step_after_pre_checkpoint_crash(tmp_path: Path) -> None:
    async def scenario() -> tuple[list[object], NeverCalledModel]:
        database = tmp_path / "sessions.db"
        repository = SQLiteSessionRepository(database)
        thread_id, turn_id = new_thread_id(), new_turn_id()
        await repository.create_thread(thread_id, tmp_path)
        user = UserMessageItem("finish recovery", turn_id)
        await repository.save_turn(TurnRecord(turn_id, thread_id, TurnStatus.RUNNING, user.content))
        await repository.append_items(thread_id, (user,))
        completed = ModelCompleted(
            (AssistantMessageItem("recovered answer", turn_id, new_step_id()),)
        )
        # Simulate process death after the business transaction committed but
        # before LangGraph wrote its node checkpoint.
        await repository.commit_model_step(thread_id, turn_id, 0, completed)
        model = NeverCalledModel()
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path),
            database_path=database,
            repository=repository,
            thread_id=thread_id,
            model=model,
        )
        events = [event async for event in runtime.resume_pending()]
        await runtime.aclose()
        return events, model

    events, model = asyncio.run(scenario())
    assert isinstance(events[0], TurnStarted) and events[0].resumed
    assert isinstance(events[-1], TurnCompleted)
    assert events[-1].final_answer == "recovered answer"
    assert model.calls == 0


def test_resume_continues_from_real_langgraph_sqlite_checkpoint(tmp_path: Path) -> None:
    async def scenario() -> list[object]:
        database = tmp_path / "sessions.db"
        settings = CorkiSettings(working_directory=tmp_path)
        repository = SQLiteSessionRepository(database)
        thread_id, turn_id = new_thread_id(), new_turn_id()
        blocking = BlockingModel()
        first = LangGraphRuntime.create(
            settings=settings,
            database_path=database,
            repository=repository,
            thread_id=thread_id,
            model=blocking,
        )
        await first._ensure_ready()
        user = UserMessageItem("continue me", turn_id)
        await repository.save_turn(TurnRecord(turn_id, thread_id, TurnStatus.RUNNING, user.content))
        await repository.append_items(thread_id, (user,))
        invocation = asyncio.create_task(
            first._compiled.ainvoke(
                _initial_state(thread_id, turn_id, settings, user),
                context=GraphRunContext(events=NullSink()),
                config=first._graph_config(turn_id),
            )
        )
        await asyncio.wait_for(blocking.started.wait(), timeout=3)
        assert first._checkpointer is not None
        assert await first._checkpointer.aget_tuple(first._graph_config(turn_id)) is not None
        invocation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await invocation
        await first.aclose()

        second_repository = SQLiteSessionRepository(database)
        second = LangGraphRuntime.create(
            settings=settings,
            database_path=database,
            repository=second_repository,
            thread_id=thread_id,
            model=AnswerModel(),
        )
        events = [event async for event in second.resume_pending()]
        await second.aclose()
        return events

    events = asyncio.run(scenario())
    assert isinstance(events[0], TurnStarted) and events[0].resumed
    assert isinstance(events[-1], TurnCompleted)
    assert events[-1].final_answer == "continued from checkpoint"


@dataclass
class DelayTool:
    name: str
    delay: float
    calls: int = 0
    cancelled: asyncio.Event | None = None
    started: asyncio.Event | None = None
    began_at: float | None = None
    ended_at: float | None = None

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            self.name,
            "test tool",
            {"type": "object", "properties": {}, "additionalProperties": False},
            concurrency=ToolConcurrency.PARALLEL,
        )

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        self.calls += 1
        self.began_at = time.monotonic()
        if self.started is not None:
            self.started.set()
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            if self.cancelled is not None:
                self.cancelled.set()
            raise
        self.ended_at = time.monotonic()
        return ToolResult(call.id, call.name, "ok")


def test_checkpoint_startup_failure_is_cleaned_up_and_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailingSaver:
        async def setup(self) -> None:
            raise OSError("temporary checkpoint failure")

    class FailingContext:
        def __init__(self) -> None:
            self.exited = False

        async def __aenter__(self) -> FailingSaver:
            return FailingSaver()

        async def __aexit__(self, *error: object) -> None:
            del error
            self.exited = True

    async def scenario() -> tuple[FailingContext, ToolRegistry]:
        original_factory = runtime_module.AsyncSqliteSaver.from_conn_string
        failed_context = FailingContext()
        calls = 0

        def factory(path: str):
            nonlocal calls
            calls += 1
            return failed_context if calls == 1 else original_factory(path)

        monkeypatch.setattr(
            runtime_module.AsyncSqliteSaver,
            "from_conn_string",
            staticmethod(factory),
        )
        registry = ToolRegistry()
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path),
            database_path=tmp_path / "retry.db",
            registry=registry,
            model=AnswerModel(),
        )

        with pytest.raises(OSError, match="temporary checkpoint failure"):
            await runtime._ensure_ready()
        assert runtime._checkpointer is None
        assert runtime._compiled is None

        # Startup failure must not freeze composition or poison the one-shot
        # LangGraph context manager used by the next initialization attempt.
        registry.register(DelayTool("late_tool", 0))
        await runtime._ensure_ready()
        assert runtime._compiled is not None
        with pytest.raises(RuntimeError, match="sealed"):
            registry.register(DelayTool("too_late", 0))
        await runtime.aclose()
        return failed_context, registry

    failed_context, registry = asyncio.run(scenario())
    assert failed_context.exited
    assert registry.get("late_tool") is not None


class ToolThenAnswerModel:
    def __init__(self, tool_names: tuple[str, ...]) -> None:
        self._tool_names = tool_names
        self._calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self._calls += 1
        turn_id = request.items[-1].turn_id
        step_id = new_step_id()
        if self._calls == 1:
            yield ModelCompleted(
                tuple(
                    ToolCallItem(ToolCall(ToolCallId(f"call-{name}"), name, {}), turn_id, step_id)
                    for name in self._tool_names
                )
            )
        else:
            yield ModelCompleted((AssistantMessageItem("done", turn_id, step_id),))

    async def aclose(self) -> None:
        return None


def test_parallel_tools_overlap_in_one_model_step(tmp_path: Path) -> None:
    async def scenario() -> tuple[DelayTool, DelayTool]:
        registry = ToolRegistry()
        first, second = DelayTool("first", 0.15), DelayTool("second", 0.15)
        registry.register(first)
        registry.register(second)
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path),
            database_path=tmp_path / "parallel.db",
            model=ToolThenAnswerModel(("first", "second")),
            registry=registry,
        )
        events = [event async for event in runtime.stream("run both")]
        await runtime.aclose()
        assert isinstance(events[-1], TurnCompleted)
        return first, second

    first, second = asyncio.run(scenario())
    assert first.began_at is not None and first.ended_at is not None
    assert second.began_at is not None and second.ended_at is not None
    assert max(first.began_at, second.began_at) < min(first.ended_at, second.ended_at)


def test_turn_cancellation_propagates_into_running_tool(tmp_path: Path) -> None:
    async def scenario() -> tuple[bool, str]:
        started, cancelled = asyncio.Event(), asyncio.Event()
        registry = ToolRegistry()
        registry.register(DelayTool("block", 60, cancelled=cancelled, started=started))
        database = tmp_path / "cancel.db"
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path),
            database_path=database,
            model=ToolThenAnswerModel(("block",)),
            registry=registry,
        )

        async def consume() -> None:
            _ = [event async for event in runtime.stream("block")]

        task = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(cancelled.wait(), timeout=2)
        await runtime.aclose()
        with SQLiteSessionRepository(database)._connect() as connection:
            status = connection.execute("SELECT status FROM turns").fetchone()[0]
        return cancelled.is_set(), status

    propagated, status = asyncio.run(scenario())
    assert propagated
    assert status == TurnStatus.CANCELLED.value


class BurstModel:
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        del request
        for _ in range(100):
            yield ModelTextDelta("x")
        await asyncio.sleep(60)

    async def aclose(self) -> None:
        return None


def test_closing_consumer_cannot_deadlock_a_full_event_queue(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, event_queue_size=1),
            database_path=tmp_path / "backpressure.db",
            model=BurstModel(),
        )
        stream = runtime.stream("burst")
        assert isinstance(await anext(stream), TurnStarted)
        _ = await anext(stream)
        await asyncio.sleep(0.05)
        await asyncio.wait_for(stream.aclose(), timeout=2)
        await runtime.aclose()

    asyncio.run(scenario())


class FailingModel:
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        del request
        raise ModelError("rate limited", kind=ModelErrorKind.RATE_LIMIT)
        yield

    async def aclose(self) -> None:
        return None


def test_runtime_preserves_typed_provider_failure(tmp_path: Path) -> None:
    async def scenario() -> TurnFailed:
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path),
            database_path=tmp_path / "failure.db",
            model=FailingModel(),
        )
        events = [event async for event in runtime.stream("fail")]
        await runtime.aclose()
        assert isinstance(events[-1], TurnFailed)
        return events[-1]

    failed = asyncio.run(scenario())
    assert failed.error == "rate limited"
    assert failed.error_kind == ModelErrorKind.RATE_LIMIT.value


class WrongTurnModel:
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        del request
        yield ModelCompleted((AssistantMessageItem("bad", new_turn_id(), new_step_id()),))

    async def aclose(self) -> None:
        return None


class DuplicateToolCallModel:
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        call_id = ToolCallId("duplicate-call")
        step_id = new_step_id()
        yield ModelCompleted(
            (
                ToolCallItem(ToolCall(call_id, "first", {}), request.items[-1].turn_id, step_id),
                ToolCallItem(ToolCall(call_id, "second", {}), request.items[-1].turn_id, step_id),
            )
        )

    async def aclose(self) -> None:
        return None


def test_malformed_provider_items_are_rejected_before_persistence(tmp_path: Path) -> None:
    async def scenario() -> tuple[TurnFailed, tuple[object, ...]]:
        database = tmp_path / "malformed.db"
        repository = SQLiteSessionRepository(database)
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path),
            database_path=database,
            repository=repository,
            model=WrongTurnModel(),
        )
        events = [event async for event in runtime.stream("test")]
        items = await repository.load_items(runtime.thread_id)
        await runtime.aclose()
        assert isinstance(events[-1], TurnFailed)
        return events[-1], items

    failed, items = asyncio.run(scenario())
    assert failed.error_kind == ModelErrorKind.PROTOCOL.value
    assert not any(isinstance(item, AssistantMessageItem) for item in items)


def test_duplicate_provider_tool_call_ids_are_rejected_before_execution(
    tmp_path: Path,
) -> None:
    async def scenario() -> TurnFailed:
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path),
            database_path=tmp_path / "duplicate-call.db",
            model=DuplicateToolCallModel(),
        )
        events = [event async for event in runtime.stream("test")]
        await runtime.aclose()
        assert isinstance(events[-1], TurnFailed)
        return events[-1]

    failed = asyncio.run(scenario())
    assert failed.error_kind == ModelErrorKind.PROTOCOL.value
    assert "duplicate tool call ids" in failed.error
