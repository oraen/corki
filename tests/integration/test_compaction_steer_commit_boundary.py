"""Input arriving during compaction must not preempt model continuation."""

import asyncio

import pytest
from test_local_compaction_inputs import COMPACT, create_runtime

from corki.models import ModelCompleted
from corki.models.types import ModelUsage
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, CompactionItem, UserMessageItem, new_step_id


@pytest.mark.parametrize("boundary", ["summary", "before_commit", "after_commit"])
@pytest.mark.parametrize("cancel", [False, True])
def test_steer_during_compaction_respects_continuation_and_survives_cold_history(
    tmp_path, monkeypatch, boundary, cancel
):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        requests, summaries = [], []

        async def hold():
            entered.set()
            await release.wait()

        class Model:
            async def stream(self, request):
                turn, step = request.items[-1].turn_id, new_step_id()
                if (
                    isinstance(request.items[-1], UserMessageItem)
                    and request.items[-1].content == COMPACT
                ):
                    summaries.append(request)
                    if boundary == "summary":
                        await hold()
                    yield ModelCompleted((AssistantMessageItem("saved progress", turn, step),))
                    return
                requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("continued", turn, step),),
                    ModelUsage(input_tokens=120_000 if len(requests) == 1 else 100),
                    end_turn=False if len(requests) == 1 else None,
                )

            async def aclose(self):
                pass

        runtime = create_runtime(tmp_path, Model())
        append = runtime._repository.append_items

        async def held_append(thread_id, items):
            installing = any(isinstance(item, CompactionItem) for item in items)
            if installing and boundary == "before_commit":
                await hold()
            result = await append(thread_id, items)
            if installing and boundary == "after_commit":
                await hold()
            return result

        monkeypatch.setattr(runtime._repository, "append_items", held_append)

        async def consume():
            return [event async for event in runtime.stream("original", realtime=True)]

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await runtime.steer("arrived during compaction")
            if cancel:
                await runtime.cancel_active()
                with pytest.raises(asyncio.CancelledError):
                    await task
                # The cancelled worker is joined; do not hold a later legitimate
                # summary for history that was never compacted successfully.
                release.set()
                returned = runtime.take_unsubmitted_inputs()
                assert [item.content for item in returned] == ["arrived during compaction"]
                assert runtime.take_unsubmitted_inputs() == ()
                archived = await runtime._repository.load_items(runtime.thread_id)
                assert not any(item.id == returned[0].id for item in archived)
                assert sum(isinstance(item, CompactionItem) for item in archived) == (
                    1 if boundary == "after_commit" else 0
                )
                thread = runtime.thread_id
                await runtime.aclose()
                cold = create_runtime(tmp_path, Model(), thread_id=thread)
                try:
                    events = [event async for event in cold.stream("after cancelled compact")]
                    assert isinstance(events[-1], TurnCompleted), events[-1]
                    assert not any(
                        isinstance(item, UserMessageItem)
                        and item.content == "arrived during compaction"
                        for item in requests[-1].items
                    )
                    assert (await cold._repository.load_items(thread))[: len(archived)] == archived
                    if boundary == "after_commit":
                        assert len(summaries) == 1, "committed summary must not be sampled again"
                finally:
                    await cold.aclose()
                return
            release.set()
            events = await asyncio.wait_for(task, 5)
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(summaries) == 1 and len(requests) == 3

            def users(request):
                return [item for item in request.items if isinstance(item, UserMessageItem)]

            assert [item.content for item in users(summaries[0])[:-1]] == ["original"]
            assert [item.content for item in users(requests[1])] == ["original"]
            final_users = users(requests[2])
            assert [item.content for item in final_users] == [
                "original",
                "arrived during compaction",
            ]
            archived = await runtime._repository.load_items(runtime.thread_id)
            assert sum(isinstance(item, CompactionItem) for item in archived) == 1
            assert sum(item.id == final_users[1].id for item in archived) == 1
            thread = runtime.thread_id
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

        cold = create_runtime(tmp_path, Model(), thread_id=thread)
        try:
            events = [event async for event in cold.stream("after reopening")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(summaries) == 1 and len(requests) == 4
            assert [item.id for item in users(requests[-1])[:2]] == [
                item.id for item in final_users
            ]
            assert (await cold._repository.load_items(thread))[: len(archived)] == archived
        finally:
            await cold.aclose()

    asyncio.run(asyncio.wait_for(scenario(), 20))
