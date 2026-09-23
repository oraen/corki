"""Equal text is not equal submission identity at a pending-input compaction."""

import asyncio

import pytest
from test_local_compaction_inputs import COMPACT, create_runtime

from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id


@pytest.mark.parametrize("pending_text", ["repeat", "different"])
def test_pending_compaction_preserves_distinct_submissions(tmp_path, monkeypatch, pending_text):
    async def scenario():
        requests, summaries = [], []

        class Model:
            async def stream(self, request):
                turn, step = request.items[-1].turn_id, new_step_id()
                if request.items[-1].content == COMPACT:
                    summaries.append(request)
                    yield ModelCompleted((AssistantMessageItem("saved progress", turn, step),))
                    return
                requests.append(request)
                if len(requests) == 1:
                    await runtime.steer(pending_text)
                yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        runtime = await create_runtime(tmp_path, Model())
        manager = runtime._graph._window_manager
        prepare = manager.prepare

        async def compact_at_pending_admission(**kwargs):
            pending = kwargs.get("pending_items", ())
            limit = manager._auto_compact_tokens
            # Force the supported pending-input compaction boundary, after the
            # original sampling request, without timing or oversized fixtures.
            if requests and pending:
                manager._auto_compact_tokens = 1
            try:
                return await prepare(**kwargs)
            finally:
                manager._auto_compact_tokens = limit

        monkeypatch.setattr(manager, "prepare", compact_at_pending_admission)
        try:
            events = [event async for event in runtime.stream("repeat", realtime=True)]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 2 and len(summaries) == 1
            original = next(i for i in requests[0].items if isinstance(i, UserMessageItem))
            summary_users = [i for i in summaries[0].items[:-1] if isinstance(i, UserMessageItem)]
            assert [i.id for i in summary_users] == [original.id]
            users = [i for i in requests[1].items if isinstance(i, UserMessageItem)]
            assert [i.content for i in users] == ["repeat", pending_text]
            assert users[0].retained_from_id == original.id
            assert users[1].id != original.id and users[1].retained_from_id is None
            archived = await runtime._repository.load_items(runtime.thread_id)
            assert sum(i.id == original.id for i in archived) == 1
            assert sum(i.id == users[1].id for i in archived) == 1
            thread = runtime.thread_id
            await runtime.aclose()
            runtime = await create_runtime(tmp_path, Model(), thread_id=thread)
            events = [event async for event in runtime.stream("after reopen")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 3 and len(summaries) == 1
            cold_users = [i for i in requests[-1].items if isinstance(i, UserMessageItem)]
            assert [i.content for i in cold_users] == ["repeat", pending_text, "after reopen"]
            assert [i.id for i in cold_users[:2]] == [i.id for i in users]
            assert (await runtime._repository.load_items(thread))[: len(archived)] == archived
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
