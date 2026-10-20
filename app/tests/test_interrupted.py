"""Interrupted output is durable desktop presentation, never model history."""

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
from content import PREVIEW_CHARS
from live import append_event, interrupted_messages
from service import Service
from test_bridge import FakeRuntime, setup

from corki.protocol.events import AssistantTextDelta, TurnCancelled, TurnStarted
from corki.protocol.ids import ThreadId, TurnId
from corki.protocol.items import UserMessageItem
from corki.sessions.models import TurnRecord, TurnStatus


def test_only_incomplete_visible_text_is_captured():
    events = []
    append_event(events, "AssistantTextDelta", {"turn_id": "t", "delta": "completed"})
    append_event(
        events,
        "AssistantMessageCompleted",
        {"turn_id": "t", "item_id": "saved", "text": "completed"},
    )
    append_event(events, "AssistantTextDelta", {"turn_id": "t", "delta": "partial"})
    append_event(
        events, "AssistantReasoningDelta", {"turn_id": "t", "delta": "private", "channel": "raw"}
    )
    assert interrupted_messages(events, "t") == [{"id": "answer:t", "content": "partial"}]


def test_stop_reopen_copy_followup_and_delete(tmp_path):
    async def check():
        setup(tmp_path)
        started, stop = asyncio.Event(), asyncio.Event()
        partial = "保留未完成正文。" * 6000

        class SlowRuntime(FakeRuntime):
            async def stream(self, message, **kw):
                turn = TurnId(str(uuid.uuid4()))
                await self.repository.create_thread(self.thread, self.settings.working_directory)
                await self.repository.save_turn(
                    TurnRecord(turn, self.thread, TurnStatus.RUNNING, message)
                )
                await self.repository.append_items(self.thread, (UserMessageItem(message, turn),))
                yield TurnStarted(self.thread, turn)
                yield AssistantTextDelta(self.thread, turn, partial, item_id="unfinished")
                started.set()
                await stop.wait()
                await self.repository.save_turn(
                    TurnRecord(turn, self.thread, TurnStatus.CANCELLED, message)
                )
                yield TurnCancelled(self.thread, turn)

            async def cancel_active(self):
                stop.set()

        async def send(event):
            pass

        async def factory(**kw):
            return SlowRuntime(**kw)

        service = Service(tmp_path, send, factory)
        thread = str(uuid.uuid4())
        request = dict(
            requestId=str(uuid.uuid4()),
            threadId=thread,
            content="question",
            provider="test",
            model="test-model",
        )
        await service.submit(request)
        await started.wait()
        task = service.tasks[thread]
        await service.stop({"threadId": thread})
        await task
        view = await service.read_thread({"threadId": thread})
        assert [m["role"] for m in view["messages"]] == ["user", "assistant", "stopped"]
        assert view["messages"][1]["content"] == partial[:PREVIEW_CHARS]
        assert view["messages"][1]["truncated"]
        assert view["messages"][1]["createdAt"]
        restored = await service.read_thread({"threadId": thread})
        assert restored["messages"][1]["createdAt"] == view["messages"][1]["createdAt"]
        from corki.protocol.items import AssistantMessageItem

        assert not any(
            isinstance(item, AssistantMessageItem)
            for item in await service.repository.load_items(ThreadId(thread))
        )
        copied, offset = "", 0
        while offset is not None:
            page = await service.read_message_text(
                {"threadId": thread, "messageId": "unfinished", "offset": offset}
            )
            copied += page["content"]
            offset = page["next"]
        assert copied == partial
        await service.close()

        async def normal(**kw):
            return FakeRuntime(**kw)

        service = Service(tmp_path, send, normal)
        try:
            assert (await service.read_thread({"threadId": thread}))["messages"] == view["messages"]
            await service.submit(
                {**request, "requestId": str(uuid.uuid4()), "content": "follow up"}
            )
            await asyncio.gather(*service.tasks.values())
            rows = (await service.read_thread({"threadId": thread}))["messages"]
            assert [m["role"] for m in rows] == [
                "user",
                "assistant",
                "stopped",
                "user",
                "assistant",
            ]
            await service.delete({"threadId": thread})
            assert (
                service.state.interrupted(thread, {view["messages"][1]["turnId"]}, PREVIEW_CHARS)
                == []
            )
        finally:
            await service.close()

    asyncio.run(check())
