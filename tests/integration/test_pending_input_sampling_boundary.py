"""Native Turn pending input does not preempt the original sampling request."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelTextDelta
from corki.protocol.events import AssistantTextDelta, TurnCompleted
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.tools import ToolRegistry


async def create_runtime(tmp_path, model):
    return await LangGraphRuntime.acreate(
        settings=CorkiSettings(
            working_directory=tmp_path, skills_enabled=False, execution_permissions=None
        ),
        registry=ToolRegistry(),
        model=model,
        home_path=tmp_path / "home",
        database_path=tmp_path / "history.db",
    )


def test_original_input_is_sampled_before_steer_queued_during_first_prepare(tmp_path, monkeypatch):
    async def scenario():
        preparing, prepared, sampled, release_model = (asyncio.Event() for _ in range(4))
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                sampled.set()
                await release_model.wait()
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = await create_runtime(tmp_path, Model())
        builder_type = type(runtime._graph._context_builder)
        build = builder_type.build
        first = True

        async def held_build(self, **kwargs):
            nonlocal first
            if first:
                first = False
                preparing.set()
                await prepared.wait()
            return await build(self, **kwargs)

        monkeypatch.setattr(builder_type, "build", held_build)

        async def consume():
            return [e async for e in runtime.stream("original", realtime=True)]

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(preparing.wait(), 2)
            await runtime.steer("queued later")
            prepared.set()
            await asyncio.wait_for(sampled.wait(), 2)
            users = [i.content for i in requests[0].items if isinstance(i, UserMessageItem)]
            assert users == ["original"], "pending input must not enter the first Turn request"
        finally:
            prepared.set()
            release_model.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("follow_up", ["tool", "model", "pending_only"])
def test_auto_compaction_resumes_model_before_draining_pending_input(tmp_path, follow_up):
    from test_local_compaction_inputs import COMPACT
    from test_local_compaction_inputs import create_runtime as compact_runtime

    from corki.models.types import ModelUsage
    from corki.protocol.items import CompactionItem, ToolCallItem, ToolResultItem
    from corki.protocol.tools import ToolCall, ToolResult, ToolSpec

    async def scenario():
        normal, summaries, calls = [], [], []

        class Effect:
            spec = ToolSpec("effect", "one observed effect", {})

            async def execute(self, call, context):
                calls.append(call.id)
                return ToolResult(call.id, call.name, "EFFECT_PROOF")

        class Model:
            async def stream(self, request):
                turn, step = request.items[-1].turn_id, new_step_id()
                if (
                    isinstance(request.items[-1], UserMessageItem)
                    and request.items[-1].content == COMPACT
                ):
                    summaries.append(request)
                    assert not any(
                        isinstance(i, UserMessageItem) and i.content == "pending"
                        for i in request.items
                    )
                    if follow_up == "tool":
                        assert any(
                            isinstance(i, ToolResultItem) and i.content == "EFFECT_PROOF"
                            for i in request.items
                        )
                    yield ModelCompleted((AssistantMessageItem("saved progress", turn, step),))
                    return
                normal.append(request)
                users = [i.content for i in request.items if isinstance(i, UserMessageItem)]
                if len(normal) == 1:
                    await runtime.steer("pending")
                    item = (
                        ToolCallItem(ToolCall("effect-once", "effect", {}), turn, step)
                        if follow_up == "tool"
                        else AssistantMessageItem("first", turn, step)
                    )
                    yield ModelCompleted(
                        (item,),
                        ModelUsage(input_tokens=120_000),
                        end_turn=False if follow_up == "model" else None,
                    )
                else:
                    if len(normal) == 2 and follow_up != "pending_only":
                        assert users == ["original"], "resume compacted work before pending user"
                    else:
                        assert users == ["original", "pending"]
                    yield ModelCompleted(
                        (AssistantMessageItem("continued", turn, step),),
                        ModelUsage(input_tokens=100),
                    )

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Effect())
        runtime = await compact_runtime(tmp_path, Model(), registry=registry)
        try:
            events = [e async for e in runtime.stream("original", realtime=True)]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(summaries) == 1
            assert len(normal) == (2 if follow_up == "pending_only" else 3)
            assert len(calls) == int(follow_up == "tool")
            history = await runtime._repository.load_items(runtime.thread_id)
            assert sum(isinstance(i, CompactionItem) for i in history) == 1
            assert (
                sum(isinstance(i, UserMessageItem) and i.content == "pending" for i in history) == 1
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("stop", ["controller", "runtime"])
def test_stop_preempts_sampling_with_queued_input_and_preserves_accepted_text(tmp_path, stop):
    async def scenario():
        entered, closed = asyncio.Event(), asyncio.Event()

        class Model:
            async def stream(self, request):
                try:
                    entered.set()
                    await asyncio.Event().wait()
                    yield ModelCompleted(())
                finally:
                    closed.set()

            async def aclose(self):
                pass

        runtime = await create_runtime(tmp_path, Model())

        async def consume():
            return [e async for e in runtime.stream("original", realtime=True)]

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await runtime.steer("pending")
            if stop == "controller":
                await runtime._realtime.stop()
            else:
                await runtime.cancel_active()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            assert closed.is_set()
            users = [
                i.content
                for i in await runtime._repository.load_items(runtime.thread_id)
                if isinstance(i, UserMessageItem)
            ]
            assert users == ["original"]
            # The cancelled comprehension never returned its terminal event list.
            # The joined Runtime still hands accepted-but-uncommitted input to the host.
            returned = runtime.take_unsubmitted_inputs()
            assert [item.content for item in returned] == ["pending"]
            assert runtime.take_unsubmitted_inputs() == ()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_steer_waits_for_sampling_completion_instead_of_discarding_the_response(tmp_path):
    async def scenario():
        prefix, release, closed = (asyncio.Event() for _ in range(3))
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn = request.items[-1].turn_id
                if len(requests) == 1:
                    try:
                        yield ModelTextDelta("prefix")
                        await release.wait()
                        yield ModelCompleted(
                            (AssistantMessageItem("first completion", turn, new_step_id()),)
                        )
                    finally:
                        closed.set()
                else:
                    assert any(
                        isinstance(i, AssistantMessageItem) and i.content == "first completion"
                        for i in request.items
                    )
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = await create_runtime(tmp_path, Model())

        async def consume():
            events = []
            async for event in runtime.stream("original", realtime=True):
                events.append(event)
                if isinstance(event, AssistantTextDelta) and event.delta == "prefix":
                    prefix.set()
            return events

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(prefix.wait(), 2)
            await runtime.steer("queued later")
            await asyncio.sleep(0.05)
            assert not closed.is_set() and len(requests) == 1, "steer must not cancel sampling"
            release.set()
            events = await asyncio.wait_for(task, 2)
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 2
            assert [i.content for i in requests[-1].items if isinstance(i, UserMessageItem)] == [
                "original",
                "queued later",
            ]
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())
