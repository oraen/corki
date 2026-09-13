"""Deterministic races at model commit, graph finalization, and terminal delivery."""

import asyncio
import threading
from pathlib import Path

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted, ModelError, ModelTextDelta
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
@pytest.mark.parametrize("max_steps", [None, 1, 4])
def test_input_accepted_before_finalization_respects_turn_budget(tmp_path: Path, phase, max_steps):
    async def scenario():
        runtime, model = make_runtime(tmp_path, max_steps=max_steps)
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
            if max_steps == 1:
                assert isinstance(events[-1], TurnFailed), events[-1]
                assert "model step limit" in events[-1].error
                assert len(model.requests) == 1
            else:
                assert isinstance(events[-1], TurnCompleted), events[-1]
                assert events[-1].final_answer == "answer-2"
                assert len(model.requests) == 2
                assert [
                    item.content
                    for item in model.requests[1].items
                    if isinstance(item, UserMessageItem)
                ] == ["initial", "late instruction"]
            history = await runtime._repository.load_items(runtime.thread_id)
            users = [item for item in history if isinstance(item, UserMessageItem)]
            assert [item.content for item in users] == ["initial", "late instruction"]
            assert len({item.id for item in users}) == 2
            assert runtime.take_unsubmitted_inputs() == ()
            assert not runtime._realtime.active
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


@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("next_realtime", [False, True])
def test_input_flush_failure_retries_storage_before_next_turn(tmp_path, committed, next_realtime):
    async def scenario():
        runtime, model = make_runtime(tmp_path, max_steps=1)
        original_commit = runtime._repository.commit_model_step
        original_append = runtime._repository.append_items
        injected = False
        failures = 0

        async def commit(*args, **kwargs):
            nonlocal injected
            await original_commit(*args, **kwargs)
            if not injected:
                injected = True
                await runtime.steer("retain after disk failure")

        async def append(thread, items):
            nonlocal failures
            if failures < 2 and any(
                isinstance(i, UserMessageItem) and i.content == "retain after disk failure"
                for i in items
            ):
                failures += 1
                if committed:
                    await original_append(thread, items)
                raise OSError("fixture input write failure")
            await original_append(thread, items)

        runtime._repository.commit_model_step = commit
        runtime._repository.append_items = append
        try:
            events = [e async for e in runtime.stream("initial", realtime=True)]
            assert isinstance(events[-1], TurnFailed)
            assert events[-1].error_kind == "storage"
            pending = runtime._realtime.unrecorded_items
            assert len(pending) == 1
            with pytest.raises(OSError, match="fixture input write failure"):
                async for _ in runtime.stream("not admitted", realtime=next_realtime):
                    pass
            assert len(model.requests) == 1
            assert runtime._realtime.unrecorded_items == pending
            events = [e async for e in runtime.stream("next", realtime=next_realtime)]
            assert isinstance(events[-1], TurnCompleted)
            assert len(model.requests) == 2
            assert sum(i.id == pending[0].id for i in model.requests[-1].items) == 1
            history = await runtime._repository.load_items(runtime.thread_id)
            assert sum(i.id == pending[0].id for i in history) == 1
            assert runtime._realtime.unrecorded_items == ()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("close_fails", [False, True])
def test_close_flushes_pending_inputs_and_can_retry_storage_only(tmp_path, close_fails):
    async def scenario():
        runtime, model = make_runtime(tmp_path, max_steps=1)
        original_commit = runtime._repository.commit_model_step
        original_append = runtime._repository.append_items
        failures = 0
        closes = 0
        retry_started = asyncio.Event()
        retry_release = asyncio.Event()

        async def commit(*args, **kwargs):
            await original_commit(*args, **kwargs)
            await runtime.steer("pending at shutdown")

        async def append(thread, items):
            nonlocal failures
            if any(
                isinstance(i, UserMessageItem) and i.content == "pending at shutdown" for i in items
            ):
                failures += 1
                if failures <= (2 if close_fails else 1):
                    raise OSError("fixture shutdown storage failure")
                if close_fails:
                    retry_started.set()
                    await retry_release.wait()
            await original_append(thread, items)

        async def close_model():
            nonlocal closes
            closes += 1

        runtime._repository.commit_model_step = commit
        runtime._repository.append_items = append
        model.aclose = close_model
        try:
            events = [e async for e in runtime.stream("initial", realtime=True)]
            assert isinstance(events[-1], TurnFailed)
            pending = runtime._realtime.unrecorded_items
            assert len(pending) == 1
            if close_fails:
                with pytest.raises(OSError, match="fixture shutdown storage failure"):
                    await runtime.aclose()
                assert runtime._realtime.unrecorded_items == pending
                with pytest.raises(RuntimeError, match="closed"):
                    async for _ in runtime.stream("must not reopen"):
                        pass
                first = asyncio.create_task(runtime.aclose())
                await asyncio.wait_for(retry_started.wait(), 2)
                second = asyncio.create_task(runtime.aclose())
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
                assert runtime._realtime.unrecorded_items == pending
                retry_release.set()
                await second
            else:
                await asyncio.gather(runtime.aclose(), runtime.aclose())
            assert runtime._realtime.unrecorded_items == ()
            assert closes == 1
            assert len(model.requests) == 1
            # Read the actual database through a fresh runtime after writer release.
            reopened = LangGraphRuntime.create(
                settings=CorkiSettings(tmp_path, skills_enabled=False),
                database_path=tmp_path / "boundary.db",
                thread_id=runtime.thread_id,
                model=AnswerModel(),
                registry=ToolRegistry(),
            )
            try:
                history = await reopened.load_display_history()
                assert sum(i.id == pending[0].id for i in history) == 1
            finally:
                await reopened.aclose()
        finally:
            retry_release.set()
            runtime._repository.append_items = original_append
            await runtime.aclose()

    asyncio.run(scenario())


def test_cancelled_input_retry_joins_database_thread_before_close(tmp_path):
    async def scenario():
        runtime, model = make_runtime(tmp_path, max_steps=1)
        original_commit = runtime._repository.commit_model_step
        original_append = runtime._repository.append_items
        sync_append = runtime._repository._append_items
        failed = False
        started = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()

        async def commit(*args, **kwargs):
            await original_commit(*args, **kwargs)
            await runtime.steer("pending threaded write")

        async def append(thread, items):
            nonlocal failed
            if not failed and any(
                isinstance(i, UserMessageItem) and i.content == "pending threaded write"
                for i in items
            ):
                failed = True
                raise OSError("initial fixture write failure")
            await original_append(thread, items)

        def blocked_append(thread, items):
            loop.call_soon_threadsafe(started.set)
            if not release.wait(5):
                raise TimeoutError("test did not release database writer")
            sync_append(thread, items)

        async def next_turn():
            return [e async for e in runtime.stream("must not sample", realtime=True)]

        runtime._repository.commit_model_step = commit
        runtime._repository.append_items = append
        retry = closing = None
        try:
            events = [e async for e in runtime.stream("initial", realtime=True)]
            assert isinstance(events[-1], TurnFailed)
            pending = runtime._realtime.unrecorded_items
            runtime._repository._append_items = blocked_append
            retry = asyncio.create_task(next_turn())
            await asyncio.wait_for(started.wait(), 2)
            retry.cancel()
            await asyncio.sleep(0)
            retry.cancel()
            await asyncio.sleep(0)
            assert not retry.done(), "cancel must join the real database write"
            closing = asyncio.create_task(runtime.aclose())
            await asyncio.sleep(0)
            assert not closing.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await retry
            await closing
            assert runtime._realtime.unrecorded_items == ()
            assert len(model.requests) == 1
            assert len(pending) == 1
        finally:
            release.set()
            if retry is not None:
                await asyncio.gather(retry, return_exceptions=True)
            if closing is not None:
                await asyncio.gather(closing, return_exceptions=True)
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

        async def accept(state, runtime, **kwargs):
            update = await original_accept(state, runtime, **kwargs)
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


@pytest.mark.parametrize(
    "outcome", ["budget", "stop", "replaced", "model_failure", "append_cancel"]
)
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
                if outcome in {"stop", "replaced"}:
                    await runtime.cancel_active(
                        reason="replaced" if outcome == "replaced" else "interrupted"
                    )
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
                assert outcome in {"stop", "replaced", "append_cancel"}
            expected = (
                TurnCancelled if outcome in {"stop", "replaced", "append_cancel"} else TurnFailed
            )
            assert isinstance(events[-1], expected), events[-1]
            assert len(model.requests) == 1
            history = await runtime._repository.load_items(runtime.thread_id)
            expected_users = {
                "stop": ["initial"],
                "replaced": ["initial"],
                "append_cancel": ["initial", "one"],
            }.get(outcome, ["initial", "one", "two"])
            assert [
                item.content for item in history if isinstance(item, UserMessageItem)
            ] == expected_users
            if outcome in {"stop", "replaced", "append_cancel"}:
                returned = events[-1].unsubmitted_inputs
                assert [item.content for item in returned] == (
                    ["two"] if outcome == "append_cancel" else ["one", "two"]
                )
                assert all(item.id not in {stored.id for stored in history} for item in returned)
                assert runtime.take_unsubmitted_inputs() == returned
                assert runtime.take_unsubmitted_inputs() == ()
                async for _ in runtime.stream("unrelated next request"):
                    pass
                assert not any(
                    item.id in {i.id for i in returned} for item in model.requests[-1].items
                )
            else:
                assert runtime.take_unsubmitted_inputs() == ()
                pending = [
                    item
                    for item in history
                    if isinstance(item, UserMessageItem) and item.content in {"one", "two"}
                ]
                async for _ in runtime.stream("explicit next request"):
                    pass
                assert len(model.requests) == 2
                for item in pending:
                    assert sum(i.id == item.id for i in model.requests[-1].items) == 1
            assert not runtime._realtime.active
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("partial", [False, True])
def test_terminal_model_stream_error_preserves_pending_without_automatic_retry(tmp_path, partial):
    async def scenario():
        runtime, model = make_runtime(tmp_path)
        original_stream = model.stream
        failed = False

        async def stream(request):
            nonlocal failed
            if not failed:
                failed = True
                model.requests.append(request)
                if partial:
                    yield ModelTextDelta("incomplete answer")
                await runtime.steer("pending instruction")
                raise ModelError("terminal fixture stream failure", retryable=False)
            async for event in original_stream(request):
                yield event

        model.stream = stream
        try:
            events = [event async for event in runtime.stream("initial", realtime=True)]
            terminals = [
                e for e in events if isinstance(e, (TurnFailed, TurnCompleted, TurnCancelled))
            ]
            assert len(terminals) == 1 and isinstance(terminals[0], TurnFailed)
            assert len(model.requests) == 1
            history = await runtime._repository.load_items(runtime.thread_id)
            users = [i for i in history if isinstance(i, UserMessageItem)]
            assert [i.content for i in users] == ["initial", "pending instruction"]
            assert not any(isinstance(i, AssistantMessageItem) for i in history)
            assert runtime.take_unsubmitted_inputs() == ()
            events = [event async for event in runtime.stream("explicit next request")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(model.requests) == 2
            assert sum(i.id == users[-1].id for i in model.requests[-1].items) == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
