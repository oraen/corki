"""Manual WeChat transport is durable, account scoped and independent of Agent context."""

import asyncio
import json

import pytest
from corki_gateway.errors import DeliveryError
from corki_gateway.history import history
from corki_gateway.service import Host
from corki_gateway.store import Store

from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.storage import SQLiteSessionRepository


def account(store, name="WeChat"):
    identity = store.save_connection({"channel": "wechat", "name": name, "values": {}})
    store.enable(identity, True)
    store.account(identity, "bot-" + identity)
    return identity


def message(identity="one", token="context-one", sender="alice"):
    return {
        "id": identity,
        "sender": sender,
        "name": sender.title(),
        "conversation": sender,
        "kind": "direct",
        "text": "Hello",
        **({"context_token": token} if token else {}),
    }


class Transport:
    def __init__(self):
        self.sent, self.wait, self.error = [], None, None

    async def call(self, method, params, **_):
        assert method == "send"
        self.sent.append(params)
        if self.wait:
            await self.wait.wait()
        if self.error:
            raise self.error
        return {"sent": True}


@pytest.mark.parametrize("token", ["fixture-context", None])
def test_send_is_idempotent_scoped_and_never_runs_agent(tmp_path, token):
    async def scenario():
        async def forbidden(*_):
            raise AssertionError("Manual messages must not execute Agent")

        host = Host(tmp_path, runner=forbidden, online=False)
        identity = account(host.store)
        transport = Transport()
        host.connectors[identity] = transport
        host.store.wechat_messages.inbound(host.store.connection(identity), message(token=token))
        view = history(host.store, identity, {}, {})
        assert view["can_send"] and view["recipient"] == "Alice"
        request = {
            "action": "manual-send",
            "id": identity,
            "session": view["thread"],
            "request_id": "fixture-request-00001",
            "text": "你好",
        }
        try:
            transport.wait = asyncio.Event()
            first = asyncio.create_task(host.change(request))
            while not transport.sent:
                await asyncio.sleep(0)
            assert (await host.change(request))["message"]["status"] == "sending"
            transport.wait.set()
            assert (await first)["message"]["status"] == "sent"
            await host.change(request)
            assert len(transport.sent) == 1
            assert transport.sent[0]["message"].get("context_token") == token
            assert "proactive" not in transport.sent[0]
            assert not host.tasks
            public = history(Store(tmp_path), identity, {}, {})
            assert [r["content"] for r in public["messages"]] == ["Hello", "你好"]
            assert public["messages"][-1]["kind"] == "manual"
            assert "fixture-context" not in json.dumps(public)
            assert not (tmp_path / "sessions/corki.db").exists()
            with pytest.raises(ValueError, match="其他消息"):
                await host.change({**request, "text": "Different"})
            other = account(host.store, "Other")
            host.connectors[other] = transport
            with pytest.raises(ValueError, match="不属于"):
                await host.change({**request, "id": other})
        finally:
            await host.close()

    asyncio.run(scenario())


def test_latest_context_failure_recovery_switch_and_relogin(tmp_path):
    async def scenario():
        host = Host(tmp_path, online=False)
        identity = account(host.store)
        transport = Transport()
        host.connectors[identity] = transport
        ledger = host.store.wechat_messages
        ledger.inbound(host.store.connection(identity), message())
        thread = history(host.store, identity, {}, {})["thread"]
        request = {
            "action": "manual-send",
            "id": identity,
            "session": thread,
            "request_id": "fixture-failure-00001",
            "text": "Manual reply",
        }
        try:
            ledger.inbound(host.store.connection(identity), message("two", "new-context"))
            transport.error = DeliveryError("微信消息发送失败：fixture code 403")
            result = await host.change(request)
            assert result["message"]["status"] == "failed"
            assert transport.sent[0]["message"]["context_token"] == "new-context"
            await host.change(request)
            assert len(transport.sent) == 1
            restored = history(Store(tmp_path), identity, {}, {})
            assert restored["messages"][-1]["delivery_failed"]
            assert "403" in restored["messages"][-1]["error"]
            host.store.enable(identity, False)
            with pytest.raises(ValueError, match="开启网关"):
                await host.change({**request, "request_id": "fixture-disabled-0001"})
            assert not history(host.store, identity, {}, {})["can_send"]
            host.store.enable(identity, True)
            host.store.account(identity, "new-bot")
            assert not history(host.store, identity, {}, {})["can_send"]
            with pytest.raises(ValueError, match="当前绑定|已重置"):
                await host.change({**request, "request_id": "fixture-relogin-00001"})
            ledger.inbound(host.store.connection(identity), message("new-bot-one", None))
            new_view = history(host.store, identity, {}, {})
            assert new_view["can_send"]
            request["session"] = new_view["thread"]
            transport.error = None
            transport.wait = asyncio.Event()
            pending = asyncio.create_task(
                host.change({**request, "request_id": "fixture-pending-00001"})
            )
            while len(transport.sent) < 2:
                await asyncio.sleep(0)
            assert "context_token" not in transport.sent[-1]["message"]
            await host.close()
            await pending
            assert not host.manual_tasks
            assert history(Store(tmp_path), identity, {}, {})["messages"][-1]["status"] == "failed"
        finally:
            await host.close()

    asyncio.run(scenario())


def test_live_agent_history_legacy_pagination_and_reset(tmp_path):
    async def scenario():
        async def run(*_):
            return "Agent reply"

        host = Host(tmp_path, runner=run, online=False)
        identity = account(host.store)
        transport = Transport()
        host.connectors[identity] = transport
        old_message = message("old")
        thread = host.store.admit(identity, old_message, str(tmp_path / "main/default"))
        repo = SQLiteSessionRepository(tmp_path / "sessions/corki.db")
        await repo.create_thread(thread, tmp_path)
        await repo.append_items(
            thread,
            (
                UserMessageItem("Old input", "old"),
                AssistantMessageItem("Old reply", "old", new_step_id()),
            ),
        )
        # The first rollout can still address a known recipient without a cached token.
        store = Store(tmp_path)
        assert history(store, identity, {}, {})["can_send"]
        try:
            result = await host.change(
                {
                    "action": "manual-send",
                    "id": identity,
                    "session": thread,
                    "request_id": "legacy-id-only-send-0001",
                    "text": "Legacy manual reply",
                }
            )
            assert result["message"]["status"] == "sent"
            assert "context_token" not in transport.sent[-1]["message"]
            await host.inbound(identity, message("new", "fresh-context"))
            await asyncio.gather(*host.tasks.values())
            assert [m["content"] for m in history(host.store, identity, {}, {})["messages"]] == [
                "Legacy manual reply",
                "Hello",
                "Agent reply",
            ]
            for index in range(205):
                host.store.wechat_messages.record(
                    identity,
                    message(),
                    "fixture:" + str(index),
                    "assistant",
                    "manual",
                    "Reply " + str(index),
                )
            all_messages, cursor = [], None
            while True:
                page = history(host.store, identity, {"before": cursor} if cursor else {}, {})
                all_messages = page["messages"] + all_messages
                cursor = page["before"]
                if cursor is None:
                    break
            assert len(all_messages) == 210
            assert len({m["id"] for m in all_messages}) == 210
            assert [m["content"] for m in all_messages[:2]] == ["Old input", "Old reply"]
            assert all_messages[-1]["content"] == "Reply 204"
            with pytest.raises(ValueError, match="游标"):
                history(host.store, identity, {"before": "legacy:9223372036854775808"}, {})
            host.store.reset_current_session(identity, thread)
            new_view = history(host.store, identity, {}, {})
            assert new_view["thread"] != thread and new_view["can_send"]
            assert new_view["messages"] == []
            with pytest.raises(ValueError, match="重置"):
                history(host.store, identity, {"thread": thread}, {})
        finally:
            await host.close()

    asyncio.run(scenario())
