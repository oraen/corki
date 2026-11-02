"""Transport transcript and manual sends never create Agent turns or duplicate side effects."""

import asyncio
import time

import pytest
from corki_gateway.errors import DeliveryError
from corki_gateway.history import history
from corki_gateway.messages import send_logged
from corki_gateway.service import Host
from corki_gateway.store import Store

from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.storage import SQLiteSessionRepository


def account(store, name="Fixture", **values):
    channel = store.save_connection(
        {
            "channel": "dingtalk",
            "name": name,
            "values": {
                "client_id": "fixture-app",
                "client_secret": "fixture-secret",
                "session_validation": "true",
                **values,
            },
        }
    )
    store.enable(channel, True)
    return channel


def message(identity="question", sender="alice"):
    return {
        "id": identity,
        "sender": sender,
        "name": sender.title(),
        "staff_id": "staff-" + sender,
        "conversation": "fixture-room",
        "kind": "direct",
        "text": "Fixture question",
    }


class Transport:
    def __init__(self):
        self.sent = []
        self.error = None
        self.wait = None

    async def call(self, method, params, **_):
        self.sent.append(params)
        if self.wait:
            await self.wait.wait()
        if self.error:
            raise self.error
        return {"sent": True}


def test_denied_input_notices_manual_send_idempotency_and_sender_isolation(tmp_path):
    async def scenario():
        async def forbidden(*_):
            raise AssertionError("manual send/authorization must never invoke Agent")

        h = Host(tmp_path, runner=forbidden, online=False)
        channel = account(h.store)
        transport = Transport()
        h.connectors[channel] = transport
        try:
            await h.inbound(channel, message())
            await asyncio.gather(*h.access_notifier.tasks.values())
            view = history(h.store, channel, {}, {})
            assert [m["role"] for m in view["messages"]] == ["user", "assistant"]
            assert view["messages"][-1]["kind"] == "notice"
            assert view["can_send"]
            session = view["thread"]
            request = {
                "action": "manual-send",
                "id": channel,
                "session": session,
                "request_id": "fixture-request-00001",
                "text": "Hello from administrator",
            }
            transport.wait = asyncio.Event()
            first = asyncio.create_task(h.change(request))
            while len(transport.sent) < 2:
                await asyncio.sleep(0)
            second = await h.change(request)
            assert second["message"]["status"] == "sending"
            transport.wait.set()
            result = await first
            assert result["message"]["status"] == "sent"
            assert transport.sent[-1]["proactive"] is True
            assert len(transport.sent) == 2
            await h.change(request)
            assert len(transport.sent) == 2
            with pytest.raises(ValueError, match="其他消息"):
                await h.change({**request, "text": "changed"})
            # Retried platform admission is one input in the transcript.
            await h.inbound(channel, message())
            assert len(history(h.store, channel, {"session": session}, {})["messages"]) == 3
            await h.inbound(channel, message("bob-question", "bob"))
            await asyncio.gather(*h.access_notifier.tasks.values())
            alice = history(h.store, channel, {"session": session}, {})
            assert all(m["sender"] == "alice" for m in alice["messages"])
            other = account(h.store, name="Other", client_id="fixture-other-app")
            h.connectors[other] = transport
            with pytest.raises(ValueError, match="不属于"):
                await h.change({**request, "id": other, "request_id": "other-request-00001"})
            assert h.store.applications(channel)["requests"][-1]["status"] == "pending"
            assert not h.tasks
        finally:
            await h.close()

    asyncio.run(scenario())


def test_failed_send_and_shutdown_are_durable_and_not_automatically_replayed(tmp_path):
    async def scenario():
        h = Host(tmp_path, online=False)
        channel = account(h.store)
        h.store.messages.contact(channel, message())
        session = history(h.store, channel, {}, {})["thread"]
        t = Transport()
        h.connectors[channel] = t
        t.error = DeliveryError("HTTP 403: missing permission")
        request = {
            "action": "manual-send",
            "id": channel,
            "session": session,
            "request_id": "failed-request-00001",
            "text": "Fixture notice",
        }
        result = await h.change(request)
        assert result["message"]["status"] == "failed"
        assert "HTTP 403" in result["message"]["error"]
        await h.change(request)
        assert len(t.sent) == 1
        restored = history(Store(tmp_path), channel, {"session": session}, {})
        assert restored["messages"][0]["delivery_failed"]
        t.error = None
        t.wait = asyncio.Event()
        pending = asyncio.create_task(h.change({**request, "request_id": "blocked-request-00001"}))
        while len(t.sent) < 2:
            await asyncio.sleep(0)
        await h.close()
        await pending
        assert not h.manual_tasks
        view = history(Store(tmp_path), channel, {"session": session}, {})
        assert view["messages"][-1]["status"] == "failed"
        assert "中断" in view["messages"][-1]["error"]

    asyncio.run(scenario())


def test_ledger_pagination_and_legacy_core_boundary_preserve_old_history(tmp_path):
    async def scenario():
        store = Store(tmp_path)
        channel = account(store, session_validation="false")
        m = message()
        thread = store.admit(channel, m, str(tmp_path / "main/default"))
        repo = SQLiteSessionRepository(tmp_path / "sessions/corki.db")
        await repo.create_thread(thread, tmp_path)
        await repo.append_items(
            thread,
            (
                UserMessageItem("Old input", "old"),
                AssistantMessageItem("Old reply", "old", new_step_id()),
            ),
        )
        with store.db() as db:
            db.execute(
                "UPDATE dingtalk_history_meta SET value=? WHERE key='cutover'", (time.time() + 1,)
            )
        store.messages.contact(channel, m)
        for i in range(205):
            store.messages.record(channel, m, "manual:" + str(i), "assistant", "manual", str(i))
        first = history(store, channel, {}, {})
        all_rows = []
        page = first
        while True:
            all_rows.extend(page["messages"])
            if page["before"] is None:
                break
            page = history(
                store,
                channel,
                {"session": first["thread"], "thread": first["thread"], "before": page["before"]},
                {},
            )
        assert len(all_rows) == 207
        assert len({r["id"] for r in all_rows}) == 207
        assert {"Old input", "Old reply"} <= {r["content"] for r in all_rows}
        # Core items after the boundary are not mirrored into the transport display.
        with store.db() as db:
            db.execute("UPDATE dingtalk_history_meta SET value=? WHERE key='cutover'", (0,))
        later = history(store, channel, {"session": first["thread"], "before": "legacy:0"}, {})
        assert later["messages"] == []

    asyncio.run(scenario())


def test_agent_final_answer_recorded_once_and_deleted_with_connection(tmp_path):
    async def scenario():
        h = Host(tmp_path, runner=lambda *_: None, online=False)
        channel = account(h.store)
        m = message()
        h.store.messages.contact(channel, m)
        h.connectors[channel] = Transport()
        try:
            await send_logged(h, channel, {"message": m, "text": "Final reply", "final": True})
            await send_logged(h, channel, {"message": m, "text": "Final reply", "final": True})
            view = history(h.store, channel, {}, {})
            assert [r["content"] for r in view["messages"]] == ["Final reply"]
            assert len(h.connectors[channel].sent) == 1
            h.store.remove_connection(channel)
            with h.store.db() as db:
                assert db.execute("SELECT count(*) FROM dingtalk_messages").fetchone()[0] == 0
                assert db.execute("SELECT count(*) FROM dingtalk_contacts").fetchone()[0] == 0
        finally:
            await h.close()

    asyncio.run(scenario())
