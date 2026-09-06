"""Deterministic races at model commit, graph finalization, and terminal delivery."""

import asyncio
from pathlib import Path

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted, ModelError
from corki.protocol.events import TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.ids import new_turn_id
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


class AnswerModel:
    def __init__(self):
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        yield ModelCompleted(
            (
                AssistantMessageItem(
                    f"answer-{len(self.requests)}", request.items[-1].turn_id, new_step_id()
                ),
            )
        )

    async def aclose(self):
        pass


def make_runtime(tmp_path, *, max_steps=4):
    model = AnswerModel()
    runtime = LangGraphRuntime.create(
        settings=CorkiSettings(
            working_directory=tmp_path, skills_enabled=False, max_steps=max_steps
        ),
        database_path=tmp_path / "boundary.db",
        model=model,
        registry=ToolRegistry(),
    )
    return runtime, model


@pytest.mark.parametrize("phase", ["model_commit", "after_evaluate", "before_finalize"])
def test_input_accepted_before_finalization_is_processed_in_same_turn(tmp_path: Path, phase):
    async def scenario():
        runtime, model = make_runtime(tmp_path)
        original = (
            runtime._repository.commit_model_step
            if phase == "model_commit"
            else (
                runtime._graph._evaluate if phase == "after_evaluate" else runtime._graph._finalize
            )
        )
        injected = False

        async def wrapped(*args, **kwargs):
            nonlocal injected
            inject = not injected
            injected = True
            if phase == "before_finalize" and inject:
                await runtime.steer("late instruction")
            result = await original(*args, **kwargs)
            if phase != "before_finalize" and inject:
                await runtime.steer("late instruction")
            return result

        if phase == "model_commit":
            runtime._repository.commit_model_step = wrapped
        else:
            # LangGraph injects context by the named runtime parameter.
            async def node(state, runtime):
                return await wrapped(state, runtime)

            if phase == "after_evaluate":
                runtime._graph._evaluate = node
            else:
                runtime._graph._finalize = node
        try:
            events = [event async for event in runtime.stream("initial", realtime=True)]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert events[-1].final_answer == "answer-2"
            assert len(model.requests) == 2
            assert [
                item.content
                for item in model.requests[1].items
                if isinstance(item, UserMessageItem)
            ] == ["initial", "late instruction"]
            assert (
                sum(
                    isinstance(event, (TurnCompleted, TurnFailed, TurnCancelled))
                    for event in events
                )
                == 1
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_durable_new_input_before_continuation_checkpoint_is_processed_after_restart(
    tmp_path: Path,
):
    async def scenario():
        first, first_model = make_runtime(tmp_path)
        recorded = asyncio.Event()
        original_evaluate = first._graph._evaluate
        original_accept = first._graph._accept_pending_realtime

        async def evaluate(state, runtime):
            result = await original_evaluate(state, runtime)
            await first.steer("durable new instruction")
            return result

        async def accept(state, runtime):
            update = await original_accept(state, runtime)
            if "user_input" in update:
                recorded.set()
                await asyncio.Event().wait()
            return update

        class NullSink:
            async def emit(self, event):
                pass

        first._graph._evaluate = evaluate
        first._graph._accept_pending_realtime = accept
        await first._ensure_ready()
        turn = new_turn_id()
        user = UserMessageItem("initial", turn)
        await first._repository.save_turn(
            TurnRecord(turn, first.thread_id, TurnStatus.RUNNING, user.content)
        )
        first._realtime.activate(turn)
        invocation = asyncio.create_task(
            first._compiled.ainvoke(
                _initial_state(first.thread_id, turn, first._settings, user, realtime=True),
                config=first._graph_config(turn),
                context=GraphRunContext(events=NullSink(), realtime=first._realtime),
            )
        )
        try:
            await asyncio.wait_for(recorded.wait(), timeout=3)
        finally:
            invocation.cancel()
            await asyncio.gather(invocation, return_exceptions=True)
        assert len(first_model.requests) == 1
        await first.aclose()
        model = AnswerModel()
        resumed = LangGraphRuntime.create(
            settings=first._settings,
            database_path=tmp_path / "boundary.db",
            thread_id=first.thread_id,
            model=model,
            registry=ToolRegistry(),
        )
        try:
            events = [event async for event in resumed.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(model.requests) == 1, "recovery finalized without sampling durable new input"
            assert [
                item.content
                for item in model.requests[0].items
                if isinstance(item, UserMessageItem)
            ] == ["initial", "durable new instruction"]
        finally:
            await resumed.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["terminal_save", "terminal_event"])
def test_steer_is_rejected_once_turn_stops_accepting_input(tmp_path: Path, phase):
    async def scenario():
        runtime, _ = make_runtime(tmp_path)
        original = runtime._repository.save_turn
        checked = False

        async def save(turn):
            nonlocal checked
            if phase == "terminal_save" and turn.status is TurnStatus.COMPLETED:
                with pytest.raises(RuntimeError, match="active|closed|accept"):
                    await runtime.steer("must not disappear")
                checked = True
            await original(turn)

        runtime._repository.save_turn = save
        try:
            events = []
            async for event in runtime.stream("initial", realtime=True):
                events.append(event)
                if phase == "terminal_event" and isinstance(event, TurnCompleted):
                    with pytest.raises(RuntimeError, match="active|closed|accept"):
                        await runtime.steer("must not disappear")
                    checked = True
            assert checked
            assert isinstance(events[-1], TurnCompleted), events[-1]
            history = await runtime._repository.load_items(runtime.thread_id)
            assert [item.content for item in history if isinstance(item, UserMessageItem)] == [
                "initial"
            ]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["budget", "stop", "model_failure", "append_cancel"])
def test_accepted_input_survives_failure_or_cancel_without_duplicate_history(
    tmp_path: Path, outcome
):
    async def scenario():
        runtime, model = make_runtime(tmp_path, max_steps=1 if outcome == "budget" else 4)
        original_commit = runtime._repository.commit_model_step
        original_append = runtime._repository.append_items
        injected = False
        append_interrupted = False

        async def commit(*args, **kwargs):
            nonlocal injected
            await original_commit(*args, **kwargs)
            if not injected:
                injected = True
                await runtime.steer("one")
                await runtime.steer("two")
                if outcome == "stop":
                    await runtime.cancel_active()
                elif outcome == "model_failure":
                    raise ModelError("fixture model commit failure")

        async def append(thread, items):
            nonlocal append_interrupted
            await original_append(thread, items)
            if (
                outcome == "append_cancel"
                and not append_interrupted
                and any(
                    isinstance(item, UserMessageItem) and item.content == "one" for item in items
                )
            ):
                append_interrupted = True
                # The first input is durable, but its graph node has not returned.
                raise asyncio.CancelledError

        runtime._repository.commit_model_step = commit
        runtime._repository.append_items = append
        try:
            events = []
            try:
                async for event in runtime.stream("initial", realtime=True):
                    events.append(event)
            except asyncio.CancelledError:
                assert outcome in {"stop", "append_cancel"}
            expected = TurnCancelled if outcome in {"stop", "append_cancel"} else TurnFailed
            assert isinstance(events[-1], expected), events[-1]
            assert len(model.requests) == 1
            history = await runtime._repository.load_items(runtime.thread_id)
            assert [item.content for item in history if isinstance(item, UserMessageItem)] == [
                "initial",
                "one",
                "two",
            ]
            assert not runtime._realtime.active
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
