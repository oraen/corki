"""Local compact.rs submits accepted history before provider-driven overflow trimming."""

import asyncio
from contextlib import suppress
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.context import ContextBuilder, ContextSnapshot, estimate_request_tokens
from corki.context.history import active_history
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelError, ModelErrorKind, ModelTextDelta
from corki.models.types import ModelUsage
from corki.protocol.events import (
    ContextCompacted,
    ModelRetryScheduled,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from corki.protocol.ids import new_tool_call_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ContextItem,
    ReasoningItem,
    ToolCallItem,
    ToolResultItem,
    TurnAbortedItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools import ToolRegistry

COMPACT = "LOCAL_COMPACTION_INPUT_TEST"


def business_items(items):
    # Existing request projection adds unknown classification to legacy assistant
    # rows. Compare every business field without mistaking that read-view metadata
    # for deleted/modified evidence; raw database rows are asserted independently.
    return tuple(
        replace(item, response_item_metadata_json=None)
        if isinstance(item, AssistantMessageItem)
        else item
        for item in items
    )


async def create_runtime(
    tmp_path, model, *, registry=None, window=200_000, retries=0, thread_id=None
):
    class Context(ContextBuilder):
        def base_instructions(self):
            return "BASE"

        async def build(self, **options):
            return ContextSnapshot("BASE", (), tmp_path)

    runtime = await LangGraphRuntime.acreate(
        settings=CorkiSettings(
            working_directory=tmp_path,
            model="fixture",
            skills_enabled=False,
            include_environment_context=False,
            context_window_tokens=window,
            auto_compact_tokens=window // 2,
            compact_prompt=COMPACT,
            model_max_retries=retries,
            model_retry_base_seconds=0.001,
        ),
        model=model,
        registry=registry or ToolRegistry(),
        database_path=tmp_path / "history.db",
        home_path=tmp_path / "home",
        thread_id=thread_id,
    )
    runtime._graph._context_builder = Context(instruction_manager=runtime._instruction_manager)
    return runtime


@pytest.mark.parametrize("phase", ["pre_turn", "mid_turn", "manual"])
def test_summary_sees_accepted_current_input_but_not_unaccepted_next_input(tmp_path, phase):
    class Effect:
        spec = ToolSpec("effect", "Record one effect", {"type": "object"})

        async def execute(self, call, context):
            return ToolResult(call.id, call.name, "OBSERVATION")

    class Model:
        def __init__(self):
            self.requests = []
            self.summaries = []
            self.normal = 0

        async def stream(self, request):
            self.requests.append(request)
            turn, step = request.items[-1].turn_id, new_step_id()
            if (
                isinstance(request.items[-1], UserMessageItem)
                and request.items[-1].content == COMPACT
            ):
                self.summaries.append(request)
                yield ModelCompleted((AssistantMessageItem("saved work", turn, step),))
                return
            self.normal += 1
            item = (
                ToolCallItem(ToolCall(new_tool_call_id(), "effect", {}), turn, step)
                if self.normal == 1
                else AssistantMessageItem("done", turn, step)
            )
            high = (phase == "mid_turn" and self.normal == 1) or (
                phase == "pre_turn" and self.normal == 2
            )
            items = (
                (
                    ReasoningItem("REASONING EVIDENCE", turn, step, summary="Reasoning summary"),
                    AssistantMessageItem("WORK IN PROGRESS", turn, step),
                    item,
                )
                if self.normal == 1
                else (item,)
            )
            yield ModelCompleted(items, ModelUsage(input_tokens=120_000 if high else 100))

        async def aclose(self):
            pass

    async def scenario():
        model, registry = Model(), ToolRegistry()
        registry.register(Effect())
        runtime = await create_runtime(tmp_path, model, registry=registry)
        try:
            assert isinstance(
                [e async for e in runtime.stream("ACCEPTED CURRENT INPUT")][-1], TurnCompleted
            )
            if phase == "manual":
                assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            assert isinstance([e async for e in runtime.stream("NEXT INPUT")][-1], TurnCompleted)
            assert len(model.summaries) == 1
            summary = model.summaries[0]
            assert any(
                isinstance(i, UserMessageItem) and i.content == "ACCEPTED CURRENT INPUT"
                for i in summary.items
            )
            assert not any(
                isinstance(i, UserMessageItem) and i.content == "NEXT INPUT" for i in summary.items
            )
            assert summary.tools == () and summary.instructions == "BASE"
            assert any(
                isinstance(i, ToolResultItem) and i.content == "OBSERVATION" for i in summary.items
            )
            assert any(
                isinstance(i, ReasoningItem) and i.content == "REASONING EVIDENCE"
                for i in summary.items
            )
            assert any(
                isinstance(i, AssistantMessageItem) and i.content == "WORK IN PROGRESS"
                for i in summary.items
            )
            summary_calls = [i.call.id for i in summary.items if isinstance(i, ToolCallItem)]
            assert summary_calls == [
                i.call_id for i in summary.items if isinstance(i, ToolResultItem)
            ]
            assert any(
                isinstance(i, UserMessageItem) and i.content == "ACCEPTED CURRENT INPUT"
                for i in model.requests[-1].items
            )
            history = await runtime._repository.load_items(runtime.thread_id)
            assert sum(isinstance(i, CompactionItem) for i in history) == 1
            assert sum(isinstance(i, ToolCallItem) for i in history) == 1
            assert sum(isinstance(i, ToolResultItem) for i in history) == 1
            assert sum(isinstance(i, ReasoningItem) for i in history) == 1
            assert not any(
                isinstance(i, (ReasoningItem, ToolCallItem, ToolResultItem))
                for i in active_history(history)
            )
            thread = runtime.thread_id
            await runtime.aclose()
            registry = ToolRegistry()
            registry.register(Effect())
            runtime = await create_runtime(tmp_path, model, registry=registry, thread_id=thread)
            assert isinstance([e async for e in runtime.stream("AFTER REOPEN")][-1], TurnCompleted)
            assert len(model.summaries) == 1
            assert not any(
                isinstance(i, (ReasoningItem, ToolCallItem, ToolResultItem))
                for i in model.requests[-1].items
            )
            assert [
                i.content for i in model.requests[-1].items if isinstance(i, UserMessageItem)
            ] == ["ACCEPTED CURRENT INPUT", "NEXT INPUT", "AFTER REOPEN"]
            assert (await runtime._repository.load_items(thread))[: len(history)] == history
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("overflow", [False, True])
def test_prompt_only_compaction_is_submitted_once_even_when_estimate_is_large(tmp_path, overflow):
    class Model:
        def __init__(self):
            self.requests = []

        async def stream(self, request):
            self.requests.append(request)
            if overflow:
                raise ModelError("provider rejected base", kind=ModelErrorKind.CONTEXT_WINDOW)
            yield ModelCompleted(
                (AssistantMessageItem("summary", request.items[-1].turn_id, new_step_id()),)
            )

        async def aclose(self):
            pass

    async def scenario():
        model = Model()
        runtime = await create_runtime(tmp_path, model, window=1000, retries=1)
        runtime._graph._context_builder.base_instructions = lambda: "x" * 8000
        try:
            events = [e async for e in runtime.compact()]
            assert isinstance(events[-1], TurnFailed if overflow else TurnCompleted)
            assert len(model.requests) == 1
            assert len(model.requests[0].items) == 1
            assert model.requests[0].items[0].content == COMPACT
            assert model.requests[0].instructions == "x" * 8000
            assert not any(isinstance(e, ModelRetryScheduled) for e in events)
            history = await runtime._repository.load_items(runtime.thread_id)
            assert sum(isinstance(i, CompactionItem) for i in history) == (not overflow)
            if overflow:
                assert events[-1].error_kind == "context_window"
                assert history == ()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_summary_read_error_survives_separate_close_failure_and_retries(tmp_path):
    async def scenario():
        requests, closed = [], []

        class Stream:
            def __init__(self, request, attempt):
                self.request, self.attempt = request, attempt

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self.attempt == 1:
                    raise ModelError("original summary read failure", kind=ModelErrorKind.TRANSPORT)
                return ModelCompleted(
                    (
                        AssistantMessageItem(
                            "VALID SUMMARY", self.request.items[-1].turn_id, new_step_id()
                        ),
                    )
                )

            async def aclose(self):
                closed.append(self.attempt)
                if self.attempt == 1:
                    raise OSError("secondary summary close failure")

        class Model:
            def stream(self, request):
                requests.append(request)
                return Stream(request, len(requests))

            async def aclose(self):
                pass

        runtime = await create_runtime(tmp_path, Model(), retries=1)
        original = UserMessageItem("KEEP ORIGINAL", new_turn_id())
        try:
            await runtime._ensure_ready()
            await runtime._repository.append_items(runtime.thread_id, (original,))
            events = [event async for event in runtime.compact()]
            assert isinstance(events[-1], TurnCompleted), events
            assert closed == [1, 2]
            assert len(requests) == 2 and requests[0].items == requests[1].items
            retries = [event for event in events if isinstance(event, ModelRetryScheduled)]
            assert len(retries) == 1 and "original summary read failure" in retries[0].error
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert stored[0] == original
            assert [item.summary for item in stored if isinstance(item, CompactionItem)] == [
                "VALID SUMMARY"
            ]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_summary_close_failure_without_cancellation_does_not_install_summary(tmp_path):
    async def scenario():
        class Model:
            async def stream(self, request):
                try:
                    yield ModelCompleted(
                        (AssistantMessageItem("SUMMARY", request.items[-1].turn_id, new_step_id()),)
                    )
                finally:
                    raise OSError("summary close failed without cancellation")

            async def aclose(self):
                pass

        runtime = await create_runtime(tmp_path, Model())
        original = UserMessageItem("KEEP THIS CONSTRAINT", new_turn_id())
        try:
            await runtime._ensure_ready()
            await runtime._repository.append_items(runtime.thread_id, (original,))
            events = [event async for event in runtime.compact()]
            assert isinstance(events[-1], TurnFailed), events
            assert "summary close failed without cancellation" in events[-1].error
            assert not any(isinstance(event, ContextCompacted) for event in events)
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert stored[0] == original
            assert not any(isinstance(item, CompactionItem) for item in stored)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("with_tool_pair", [False, True])
@pytest.mark.parametrize("partial_summary", [False, True])
@pytest.mark.parametrize("close_error", [False, True])
def test_cancellation_of_large_summary_request_preserves_raw_history(
    tmp_path, with_tool_pair, partial_summary, close_error
):
    async def scenario():
        entered = asyncio.Event()
        closed = asyncio.Event()
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                try:
                    if partial_summary:
                        yield ModelTextDelta("UNFINISHED SUMMARY")
                    entered.set()
                    await asyncio.Event().wait()
                    yield
                finally:
                    closed.set()
                    if close_error:
                        raise OSError("summary stream close failed")

            async def aclose(self):
                pass

        runtime = await create_runtime(tmp_path, Model(), window=1000)
        original = (AssistantMessageItem("evidence " * 2000, new_turn_id(), new_step_id()),)
        if with_tool_pair:
            turn, step = new_turn_id(), new_step_id()
            call = ToolCall(new_tool_call_id(), "read", {"path": "evidence"})
            original += (
                ToolCallItem(call, turn, step),
                ToolResultItem(call.id, call.name, "ORIGINAL TOOL RESULT", turn, step),
                UserMessageItem("KEEP THIS CONSTRAINT", new_turn_id()),
            )

        async def consume():
            async for event in runtime.compact():
                events.append(event)

        events = []
        await runtime._ensure_ready()
        await runtime._repository.append_items(runtime.thread_id, original)
        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert business_items(requests[0].items[:-1]) == original
            if close_error:
                await runtime.cancel_active()
            else:
                task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert closed.is_set() and len(requests) == 1
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert stored[: len(original)] == original
            assert len(stored) == len(original) + 1 and isinstance(stored[-1], TurnAbortedItem)
            assert not any(isinstance(item, CompactionItem) for item in stored)
            assert sum(isinstance(event, TurnCancelled) for event in events) == 1
            assert not any(
                isinstance(event, (ContextCompacted, TurnCompleted, TurnFailed)) for event in events
            )
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["read", "close"])
@pytest.mark.parametrize("trigger", ["manual", "pre_turn", "after_tool"])
def test_summary_cancellation_cannot_be_hidden_by_a_normal_model_return(tmp_path, phase, trigger):
    async def scenario():
        entered, closed = asyncio.Event(), asyncio.Event()
        requests = []
        effects = []
        automatic = trigger != "manual"
        call_id = new_tool_call_id()
        observation = "COMMITTED FACT " + " observation" * 6000

        class Effect:
            spec = ToolSpec("effect", "Read a large observation", {"type": "object"})

            async def execute(self, call, context):
                effects.append(call.id)
                return ToolResult(call.id, call.name, observation)

        class Model:
            async def stream(self, request):
                requests.append(request)
                if trigger == "after_tool" and len(requests) == 1:
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(call_id, "effect", {}),
                                request.items[-1].turn_id,
                                new_step_id(),
                            ),
                        ),
                        ModelUsage(input_tokens=6000),
                    )
                    return
                assert request.items[-1].content == COMPACT
                assert request.tools == ()
                if trigger == "after_tool":
                    assert any(
                        isinstance(item, ToolResultItem) and "COMMITTED FACT" in item.content
                        for item in request.items
                    )
                try:
                    if phase == "read":
                        entered.set()
                        with suppress(asyncio.CancelledError):
                            await asyncio.Future()
                    yield ModelCompleted(
                        (
                            AssistantMessageItem(
                                "CANCELLED SUMMARY", request.items[-1].turn_id, new_step_id()
                            ),
                        )
                    )
                finally:
                    if phase == "close":
                        entered.set()
                        with suppress(asyncio.CancelledError):
                            await asyncio.Future()
                    closed.set()

            async def aclose(self):
                pass

        registry = ToolRegistry()
        if trigger == "after_tool":
            registry.register(Effect())
        runtime = await create_runtime(
            tmp_path, Model(), registry=registry, window=8000 if automatic else 200_000
        )
        original = UserMessageItem(
            "KEEP ORIGINAL CONSTRAINT" + (" old history" * 6000 if trigger == "pre_turn" else ""),
            new_turn_id(),
        )
        events = []

        async def consume():
            stream = runtime.stream("CURRENT REQUEST") if automatic else runtime.compact()
            async for event in stream:
                events.append(event)

        await runtime._ensure_ready()
        await runtime._repository.append_items(runtime.thread_id, (original,))
        task = asyncio.create_task(consume())
        try:
            try:
                await asyncio.wait_for(entered.wait(), 5)
            except TimeoutError:
                failures = [event for event in events if isinstance(event, TurnFailed)]
                pytest.fail(f"Summary not reached: {failures!r}; requests={len(requests)}")
            await asyncio.wait_for(runtime.cancel_active(), 5)
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert not any(isinstance(item, CompactionItem) for item in stored)
            with pytest.raises(asyncio.CancelledError):
                await task
            assert closed.is_set() and len(requests) == (2 if trigger == "after_tool" else 1)
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert stored[0] == original
            assert len(stored) == {"manual": 2, "pre_turn": 3, "after_tool": 6}[trigger]
            assert isinstance(stored[-1], TurnAbortedItem)
            if automatic:
                current = stored[2] if trigger == "after_tool" else stored[1]
                assert isinstance(current, UserMessageItem)
                assert current.content == "CURRENT REQUEST"
            if trigger == "after_tool":
                assert effects == [call_id]
                assert isinstance(stored[1], ContextItem)
                assert isinstance(stored[3], ToolCallItem)
                assert stored[3].call.id == call_id
                assert isinstance(stored[4], ToolResultItem)
                assert stored[4].content == observation
            assert not any(isinstance(item, CompactionItem) for item in stored)
            assert sum(isinstance(event, TurnCancelled) for event in events) == 1
            assert not any(
                isinstance(event, (ContextCompacted, TurnCompleted, TurnFailed)) for event in events
            )
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

        if trigger == "after_tool":
            cold = await create_runtime(tmp_path, Model(), thread_id=runtime.thread_id, window=8000)
            try:
                assert [event async for event in cold.resume_pending()] == []
                assert await cold._repository.load_items(cold.thread_id) == stored
                assert len(requests) == 2 and effects == [call_id]
            finally:
                await cold.aclose()

    asyncio.run(scenario())


def test_provider_overflow_removes_pair_and_resets_local_retry_budget(tmp_path):
    class Model:
        def __init__(self):
            self.requests = []
            self.closed = []

        async def stream(self, request):
            self.requests.append(request)
            try:
                attempt = len(self.requests)
                if attempt in (1, 3):
                    raise ModelError("stream failed", kind=ModelErrorKind.TRANSPORT)
                if attempt == 2:
                    raise ModelError("window exceeded", kind=ModelErrorKind.CONTEXT_WINDOW)
                yield ModelCompleted(
                    (AssistantMessageItem("summary", request.items[-1].turn_id, new_step_id()),)
                )
            finally:
                self.closed.append(request)

        async def aclose(self):
            pass

    async def scenario():
        model = Model()
        runtime = await create_runtime(tmp_path, model, window=1000, retries=1)
        old = new_turn_id()
        call = ToolCall(new_tool_call_id(), "effect", {})
        original = (
            ToolCallItem(call, old, new_step_id()),
            ToolResultItem(call.id, call.name, "x" * 8000, old),
            AssistantMessageItem("RECENT FACT", old, new_step_id()),
        )
        try:
            await runtime._ensure_ready()
            await runtime._repository.append_items(runtime.thread_id, original)
            events = [e async for e in runtime.compact()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(model.requests) == 4 and model.closed == model.requests
            for request in model.requests[:2]:
                assert business_items(request.items[:-1]) == original
            for request in model.requests[2:]:
                assert business_items(request.items[:-1]) == original[2:]
            notices = [e for e in events if isinstance(e, ModelRetryScheduled)]
            assert [e.attempt for e in notices] == [1, 1]
            assert all(e.purpose == "compaction" for e in notices)
            assert (await runtime._repository.load_items(runtime.thread_id))[:3] == original
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("overflow", [False, True])
def test_manual_summary_submits_full_history_before_real_provider_overflow(tmp_path, overflow):
    class Model:
        def __init__(self):
            self.requests = []

        async def stream(self, request):
            self.requests.append(request)
            if overflow and len(self.requests) == 1:
                raise ModelError("provider overflow", kind=ModelErrorKind.CONTEXT_WINDOW)
            yield ModelCompleted(
                (AssistantMessageItem("summary", request.items[-1].turn_id, new_step_id()),)
            )

        async def aclose(self):
            pass

    async def scenario():
        model = Model()
        runtime = await create_runtime(tmp_path, model, window=1000)
        old = new_turn_id()
        original = (
            AssistantMessageItem("OLDEST EVIDENCE " + "x" * 8000, old, new_step_id()),
            UserMessageItem("RECENT INPUT", old),
        )
        try:
            await runtime._repository.create_thread(runtime.thread_id, tmp_path)
            await runtime._repository.append_items(runtime.thread_id, original)
            events = [e async for e in runtime.compact()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert business_items(model.requests[0].items[:-1]) == original
            assert estimate_request_tokens("BASE", model.requests[0].items, ()) > 950
            assert len(model.requests) == (2 if overflow else 1)
            if overflow:
                assert business_items(model.requests[1].items[:-1]) == original[1:]
            assert (await runtime._repository.load_items(runtime.thread_id))[:2] == original
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
