"""Unsupported formats receive configured transport replies, never Agent turns."""

import asyncio

import pytest
from corki_gateway.errors import DeliveryError
from corki_gateway.fallback import DEFAULT_REPLY
from corki_gateway.history import history
from corki_gateway.service import Host
from corki_gateway.store import Store


def account(store, **values):
    channel = store.save_connection(
        {
            "channel": "dingtalk",
            "name": "Fixture",
            "values": {
                "client_id": "fixture-app",
                "client_secret": "fixture-secret",
                "session_validation": "false",
                **values,
            },
        }
    )
    store.enable(channel, True)
    return channel


def message(identity="video"):
    return {
        "id": identity,
        "sender": "fixture-sender",
        "staff_id": "fixture-user",
        "name": "Fixture user",
        "conversation": "fixture-room",
        "kind": "direct",
        "text": "[视频消息]",
        "unsupported": True,
    }


class Transport:
    def __init__(self):
        self.sent = []
        self.error = None
        self.wait = None

    async def call(self, method, params, **_):
        assert method == "send"
        self.sent.append(params)
        if self.wait:
            await self.wait.wait()
        if self.error:
            raise self.error
        return {"sent": True}


async def forbidden(*_, **__):
    raise AssertionError("Unsupported media must not invoke Agent")


def test_default_custom_blank_dedup_and_live_settings_without_reconnect(tmp_path):
    async def scenario():
        h = Host(tmp_path, runner=forbidden, online=False)
        channel = account(h.store)
        transport = Transport()
        h.connectors[channel] = transport
        try:
            transport.wait = asyncio.Event()
            assert (await h.inbound(channel, message()))["unsupported"]
            assert (await h.inbound(channel, message()))["duplicate"]
            transport.wait.set()
            await asyncio.gather(*h.tasks.values())
            assert len(transport.sent) == 1
            assert transport.sent[0]["text"] == DEFAULT_REPLY
            assert transport.sent[0]["fallback_proactive"] is True
            assert (await h.inbound(channel, message()))["duplicate"]
            before = h.store.connection(channel)
            for identity, text, expected in [
                ("custom", "请发送文字或图片", "请发送文字或图片"),
                ("blank", "   ", DEFAULT_REPLY),
            ]:
                await h.change(
                    {
                        "action": "save-connection",
                        "id": channel,
                        "name": "Fixture",
                        "values": {"unsupported_reply": text},
                    }
                )
                after = h.store.connection(channel)
                assert before["conversation_id"] == after["conversation_id"]
                assert h.connectors[channel] is transport
                await h.inbound(channel, message(identity))
                await asyncio.gather(*h.tasks.values())
                assert transport.sent[-1]["text"] == expected
            view = history(Store(tmp_path), channel, {}, {})
            assert len(view["messages"]) == 6
            assert [m["kind"] for m in view["messages"]].count("fallback") == 3
            with h.store.db() as db:
                assert db.execute("SELECT count(*) FROM routes").fetchone()[0] == 0
                assert (
                    db.execute("SELECT count(*) FROM receipts WHERE status='completed'").fetchone()[
                        0
                    ]
                    == 3
                )
        finally:
            await h.close()

    asyncio.run(scenario())


def test_authorization_precedes_fallback(tmp_path):
    async def scenario():
        h = Host(tmp_path, runner=forbidden, online=False)
        channel = account(h.store, session_validation="true", unsupported_reply="Format fallback")
        transport = Transport()
        h.connectors[channel] = transport
        try:
            assert (await h.inbound(channel, message()))["access_denied"]
            await asyncio.gather(*h.access_notifier.tasks.values())
            assert len(transport.sent) == 1
            assert transport.sent[0]["text"] != "Format fallback"
            assert not h.tasks
        finally:
            await h.close()

    asyncio.run(scenario())


def test_failed_or_cancelled_delivery_never_replays_and_keeps_diagnostics(tmp_path):
    async def scenario():
        h = Host(tmp_path, runner=forbidden, online=False)
        channel = account(h.store)
        transport = Transport()
        h.connectors[channel] = transport
        transport.error = DeliveryError("HTTP 403: fixture permission denied")
        try:
            await h.inbound(channel, message())
            await asyncio.gather(*h.tasks.values())
            assert (await h.inbound(channel, message()))["duplicate"]
            assert len(transport.sent) == 1
            view = history(h.store, channel, {}, {})
            assert view["messages"][-1]["delivery_failed"]
            assert "HTTP 403" in view["messages"][-1]["error"]
            transport.error = None
            transport.wait = asyncio.Event()
            await h.inbound(channel, message("stopped"))
            while len(transport.sent) < 2:
                await asyncio.sleep(0)
            await h.close()
            with h.store.db() as db:
                assert (
                    db.execute("SELECT status FROM receipts WHERE id='stopped'").fetchone()[0]
                    == "cancelled"
                )
            assert not h.tasks
        finally:
            await h.close()

    asyncio.run(scenario())


def test_config_limits_and_strict_unsupported_flag(tmp_path):
    async def scenario():
        h = Host(tmp_path, online=False)
        channel = account(h.store)
        try:
            with pytest.raises(ValueError, match="2000"):
                h.store.save_connection(
                    {"id": channel, "name": "Fixture", "values": {"unsupported_reply": "x" * 2001}}
                )
            with pytest.raises(ValueError, match="类型标记"):
                await h.inbound(channel, {**message(), "unsupported": "true"})
        finally:
            await h.close()

    asyncio.run(scenario())
