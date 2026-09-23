import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

import corki.core.runtime as runtime_module
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import (
    ModelCompleted,
    ModelError,
    ModelErrorKind,
    ModelItemCompleted,
    ModelRequest,
    ModelTextDelta,
)
from corki.models.types import ModelEvent
from corki.protocol.events import TurnCompleted, TurnFailed, TurnStarted
from corki.protocol.ids import ToolCallId, new_thread_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ReasoningItem,
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


@pytest.mark.parametrize(
    "streamed_items, pending_commit", [(False, False), (True, False), (True, True)]
)
@pytest.mark.parametrize("via_cli", [False, True])
@pytest.mark.parametrize("history_padding, warmup_steps", [(0, 0), (120, 0), (0, 26)])
def test_mixed_output_commit_recovers_without_repeating_tools(
    tmp_path, monkeypatch, streamed_items, pending_commit, via_cli, history_padding, warmup_steps
):
    async def scenario():
        import threading

        settings = CorkiSettings(
            working_directory=tmp_path, skills_enabled=False, plugins_enabled=False
        )
        database = tmp_path / "mixed.db"
        finished = {name: asyncio.Event() for name in ("first", "second")}
        calls = {name: 0 for name in finished}

        class Tool:
            def __init__(self, name):
                self.spec = ToolSpec(
                    name, "fixture", {"type": "object"}, concurrency=ToolConcurrency.PARALLEL
                )

            async def execute(self, call, context):
                calls[call.name] += 1
                finished[call.name].set()
                return ToolResult(call.id, call.name, f"result-{call.name}")

        class Model:
            def __init__(self, recovering=False):
                self.recovering, self.calls = recovering, 0
                self.committed = ()

            async def stream(self, request):
                self.calls += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if self.recovering:
                    results = [i for i in request.items if isinstance(i, ToolResultItem)]
                    assert [i.content for i in results] == ["result-first", "result-second"]
                    assert sum(isinstance(i, ReasoningItem) for i in request.items) == 1
                    assert (
                        sum(
                            isinstance(i, AssistantMessageItem) and i.content == "Working"
                            for i in request.items
                        )
                        == 1
                    )
                    yield ModelCompleted((AssistantMessageItem("Finished", turn, step),))
                    return
                if self.calls <= warmup_steps:
                    yield ModelCompleted(
                        (AssistantMessageItem(f"Warmup {self.calls}", turn, step),), end_turn=False
                    )
                    return
                self.committed = (
                    ReasoningItem("reasoning", turn, step, summary="Summary"),
                    AssistantMessageItem("Working", turn, step),
                    *(
                        ToolCallItem(ToolCall(ToolCallId(f"call-{name}"), name, {}), turn, step)
                        for name in calls
                    ),
                )
                if streamed_items:
                    for item in self.committed:
                        yield ModelItemCompleted(item)
                    for event in finished.values():
                        await event.wait()
                yield ModelCompleted(self.committed)

            async def aclose(self):
                pass

        def registry():
            result = ToolRegistry()
            for name in calls:
                result.register(Tool(name))
            return result

        warm_model = Model()
        warm = await LangGraphRuntime.acreate(
            settings=settings, database_path=database, model=warm_model, registry=registry()
        )
        await warm._ensure_ready()
        thread, turn = warm.thread_id, new_turn_id()
        user = UserMessageItem("Recover mixed output", turn)
        repository = warm._repository
        commit_started = asyncio.Event()
        commit_release = threading.Event()
        loop = asyncio.get_running_loop()
        complete = repository._complete_tool_call

        def gated_complete(*args):
            loop.call_soon_threadsafe(commit_started.set)
            if not commit_release.wait(5):
                raise TimeoutError("test did not release tool result commit")
            return complete(*args)

        if pending_commit:
            monkeypatch.setattr(repository, "_complete_tool_call", gated_complete)
        await repository.save_turn(TurnRecord(turn, thread, TurnStatus.RUNNING, user.content))
        await repository.append_items(thread, (user,))
        reached = asyncio.Event()
        original = type(repository).commit_model_step

        async def stop_after_commit(self, *args, **kwargs):
            await original(self, *args, **kwargs)
            if args[2] == warmup_steps:
                reached.set()
                await asyncio.Event().wait()

        try:
            with monkeypatch.context() as patch:
                patch.setattr(type(repository), "commit_model_step", stop_after_commit)
                work = asyncio.create_task(
                    warm._compiled.ainvoke(
                        _initial_state(thread, turn, settings, user),
                        context=GraphRunContext(events=NullSink()),
                        config=warm._graph_config(turn),
                    )
                )
                try:
                    await asyncio.wait_for(reached.wait(), 15)
                    if pending_commit:
                        await asyncio.wait_for(commit_started.wait(), 5)
                finally:
                    work.cancel()
                    try:
                        if pending_commit:
                            done, _ = await asyncio.wait((work,), timeout=0.1)
                            assert not done, "cancellation abandoned an active result commit"
                            work.cancel()
                            done, _ = await asyncio.wait((work,), timeout=0.1)
                            assert not done, "repeated cancellation abandoned result commit"
                    finally:
                        commit_release.set()
                        with pytest.raises(asyncio.CancelledError):
                            await work
            assert warm_model.calls == warmup_steps + 1
            assert calls == dict.fromkeys(calls, int(streamed_items))
        finally:
            await warm.aclose()
        if history_padding:
            padded = SQLiteSessionRepository(database)
            try:
                await padded.append_items(
                    thread,
                    tuple(
                        AssistantMessageItem(f"history filler {index}", turn, new_step_id())
                        for index in range(history_padding)
                    ),
                )
            finally:
                await padded.close()
        model = Model(recovering=True)
        cold = await LangGraphRuntime.acreate(
            settings=settings,
            database_path=database,
            thread_id=thread,
            model=model,
            registry=registry(),
        )
        events = []

        async def verify_recovered():
            assert isinstance(events[-1], TurnCompleted), events
            assert model.calls == 1 and calls == dict.fromkeys(calls, 1)
            items = await cold._repository.load_items(thread)
            for item in (user, *warm_model.committed):
                assert sum(stored.id == item.id for stored in items) == 1
                assert item in items
            assert sum(isinstance(item, ToolResultItem) for item in items) == 2
            assert [
                item.content
                for item in items
                if isinstance(item, AssistantMessageItem) and item.content.startswith("Warmup ")
            ] == [f"Warmup {i}" for i in range(1, warmup_steps + 1)]
            assert await cold._repository.latest_running_turn(thread) is None
            assert [event async for event in cold.resume_pending()] == []

        try:
            if via_cli:
                resume = cold.resume_pending

                async def observed_resume():
                    async for event in resume():
                        events.append(event)
                        yield event

                cold.resume_pending = observed_resume

                class UI(TerminalUI):
                    async def read_message(self):
                        if not self._mode_cycle_enabled:
                            # Recovery now owns an active composer. No fixture input
                            # arrives until the completed turn returns to idle.
                            await asyncio.Future()
                        await verify_recovered()
                        if history_padding:
                            assert "Summary" not in self._transcript.render(
                                80, include_reasoning=True
                            )
                            pager = self._history_view.loader
                            pager.request_older(beginning=True)
                            await pager.task
                        raise EOFError

                ui = UI(settings, tmp_path / "history", console=Console(file=StringIO()))
                app = CorkiApplication(settings, CorkiPaths.from_home(tmp_path), cold, ui)
                assert await app.run() == 0
                for width in (40, 100):
                    rendered = ui._transcript.render(width, include_reasoning=True)
                    for text in ("Summary", "Working", "Finished", "result-first", "result-second"):
                        assert rendered.count(text) == 1
                assert not ui._reasoning_active
                assert list(ui._session.history.get_strings()) == []
            else:
                events.extend([event async for event in cold.resume_pending()])
                await verify_recovered()
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@dataclass
class FakeNodeRuntime:
    context: GraphRunContext


def test_replayed_completed_tool_result_has_one_durable_history_item(tmp_path: Path) -> None:
    async def scenario() -> tuple[tuple[object, ...], int]:
        registry = ToolRegistry()
        tool = DelayTool("stable", 0)
        registry.register(tool)
        repository = SQLiteSessionRepository(tmp_path / "stable.db")
        runtime = await LangGraphRuntime.acreate(
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


@pytest.mark.parametrize("committed", [False, True])
def test_running_tool_checkpoint_resumes_without_repeating_effect(tmp_path, monkeypatch, committed):
    from langgraph.errors import NodeCancelledError

    async def scenario():
        settings = CorkiSettings(tmp_path, skills_enabled=False, plugins_enabled=False)
        database = tmp_path / "tool-window.db"
        effects, requests = [], []
        call = ToolCall(ToolCallId("effect-call"), "effect", {})

        class Effect:
            spec = ToolSpec("effect", "effect before interruption", {"type": "object"})

            async def execute(self, incoming, context):
                effects.append(incoming.id)
                if not committed:
                    raise asyncio.CancelledError("after effect before result")
                return ToolResult(incoming.id, incoming.name, "committed effect")

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    results = [i for i in request.items if isinstance(i, ToolResultItem)]
                    assert len(results) == 1
                    assert results[0].is_error == (not committed)
                    assert ("committed effect" in results[0].content) == committed
                    if not committed:
                        assert "unknown" in results[0].content
                    yield ModelCompleted((AssistantMessageItem("recovered", turn, step),))

            async def aclose(self):
                pass

        async def create(thread=None):
            registry = ToolRegistry()
            registry.register(Effect())
            return await LangGraphRuntime.acreate(
                settings=settings,
                database_path=database,
                registry=registry,
                model=Model(),
                thread_id=thread,
                home_path=tmp_path / "home",
            )

        warm = await create()
        turn = new_turn_id()
        user = UserMessageItem("perform once", turn)
        complete = warm._repository.complete_tool_call

        async def stop_after_commit(*args, **kwargs):
            await complete(*args, **kwargs)
            raise asyncio.CancelledError("after durable result before checkpoint")

        if committed:
            monkeypatch.setattr(warm._repository, "complete_tool_call", stop_after_commit)
        try:
            await warm._ensure_ready()
            await warm._repository.save_turn(
                TurnRecord(turn, warm.thread_id, TurnStatus.RUNNING, user.content)
            )
            await warm._repository.append_items(warm.thread_id, (user,))
            with pytest.raises(NodeCancelledError, match="execute_tools"):
                await warm._compiled.ainvoke(
                    _initial_state(warm.thread_id, turn, settings, user),
                    context=GraphRunContext(events=NullSink()),
                    config=warm._graph_config(turn),
                )
            assert effects == [call.id] and len(requests) == 1
            assert await warm._repository.latest_running_turn(warm.thread_id) is not None
            assert await warm._checkpointer.aget_tuple(warm._graph_config(turn)) is not None
        finally:
            await warm.aclose()

        cold = await create(warm.thread_id)
        try:
            events = [event async for event in cold.resume_pending()]
            assert isinstance(events[0], TurnStarted) and events[0].resumed
            assert isinstance(events[-1], TurnCompleted) and events[-1].turn_id == turn
            assert effects == [call.id] and len(requests) == 2
            history = await cold._repository.load_items(cold.thread_id)
            assert sum(isinstance(i, UserMessageItem) for i in history) == 1
            assert sum(isinstance(i, ToolResultItem) for i in history) == 1
            assert [event async for event in cold.resume_pending()] == []
        finally:
            await cold.aclose()

    asyncio.run(asyncio.wait_for(scenario(), 15))


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
        runtime = await LangGraphRuntime.acreate(
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
        first = await LangGraphRuntime.acreate(
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
        second = await LangGraphRuntime.acreate(
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
        runtime = await LangGraphRuntime.acreate(
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
        runtime = await LangGraphRuntime.acreate(
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
        runtime = await LangGraphRuntime.acreate(
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
        runtime = await LangGraphRuntime.acreate(
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
        runtime = await LangGraphRuntime.acreate(
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
    def __init__(self, flavor="wrong_turn"):
        self.flavor = flavor

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        turn = new_turn_id() if self.flavor == "wrong_turn" else request.items[-1].turn_id
        first = AssistantMessageItem("bad", turn, new_step_id())
        items = (first,)
        if self.flavor == "multiple_steps":
            items = (first, AssistantMessageItem("also bad", turn, new_step_id()))
        elif self.flavor == "duplicate_items":
            items = (first, first)
        yield ModelCompleted(items)

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


@pytest.mark.parametrize("flavor", ["wrong_turn", "multiple_steps", "duplicate_items"])
def test_malformed_provider_items_are_rejected_before_persistence(tmp_path: Path, flavor) -> None:
    async def scenario() -> tuple[TurnFailed, tuple[object, ...]]:
        database = tmp_path / "malformed.db"
        repository = SQLiteSessionRepository(database)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=tmp_path),
            database_path=database,
            repository=repository,
            model=WrongTurnModel(flavor),
        )
        events = [event async for event in runtime.stream("test")]
        items = await repository.load_items(runtime.thread_id)
        assert await repository.latest_running_turn(runtime.thread_id) is None
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
        runtime = await LangGraphRuntime.acreate(
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
