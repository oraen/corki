import asyncio

import pytest
from corki_gateway.reply_card import ReplyCard
from corki_gateway.store import Store

OPTIONS = {"enabled": True, "template": "fixture.schema", "field": "content"}


def test_card_configuration_does_not_reset_conversation_or_access(tmp_path):
    store = Store(tmp_path)
    identity = store.save_connection({"channel": "dingtalk", "name": "Fixture", "values": {}})
    previous = store.connection(identity)
    store.save_connection(
        {
            "id": identity,
            "name": "Fixture",
            "values": {
                "ai_card_enabled": "true",
                "ai_card_template": "fixture.schema",
            },
        }
    )
    current = store.connection(identity)
    assert previous["conversation_id"] == current["conversation_id"]
    assert store.execution_config(previous, previous["config"]) == store.execution_config(
        current, current["config"]
    )
    for values in (
        {"ai_card_template": "../bad"},
        {"ai_card_enabled": "true", "ai_card_template": ""},
    ):
        with pytest.raises(ValueError):
            store.save_connection({"id": identity, "name": "Fixture", "values": values})
    with pytest.raises(ValueError):
        store.save_connection(
            {"channel": "wechat", "name": "Other", "values": {"ai_card_enabled": "true"}}
        )


def test_optional_progress_is_bounded_nonblocking_and_never_passes_raw_reasoning():
    async def scenario():
        from corki.protocol.events import AssistantReasoningDelta, AssistantTextDelta

        calls = []
        gate = asyncio.Event()

        class Connector:
            async def call(self, method, params, timeout):
                calls.append(params)
                await gate.wait()
                return {"supported": True}

        card = ReplyCard(Connector(), {"id": "fixture"}, OPTIONS)
        card.offer()
        await asyncio.sleep(0)
        for _ in range(100):
            card.offer(AssistantReasoningDelta("thread", "turn", "private", "id", channel="raw"))
            card.offer(AssistantTextDelta("thread", "turn", "commentary", "id", phase="commentary"))
        assert len(calls) == 1
        gate.set()
        await card.settle()
        assert all(p["text"] == "" for p in calls)
        await card.close()

        class Broken:
            async def call(self, *args, **kwargs):
                raise OSError("fixture")

        card = ReplyCard(Broken(), {"id": "fixture"}, OPTIONS)
        card.offer()
        await card.settle()
        assert not card.supported

    asyncio.run(scenario())


def test_host_card_lifecycle_success_failure_and_cancellation(tmp_path):
    async def scenario():
        from corki_gateway.service import Host

        gate = asyncio.Event()
        running = asyncio.Event()
        mode = "success"

        async def runner(*args):
            running.set()
            if mode == "cancel":
                await gate.wait()
            if mode == "failure":
                raise ValueError("Agent fixture failure")
            return "Fixture answer"

        host = Host(tmp_path, runner=runner, online=False)
        identity = host.store.save_connection(
            {
                "channel": "dingtalk",
                "name": "Fixture",
                "values": {
                    "client_id": "fixture-id",
                    "client_secret": "fixture-secret",
                    "session_validation": "false",
                    "ai_card_enabled": "true",
                    "ai_card_template": "fixture.schema",
                },
            }
        )
        host.store.enable(identity, True)

        class Connector:
            def __init__(self):
                self.calls = []

            async def call(self, method, params, timeout=45):
                self.calls.append((method, params))
                return {"supported": True} if method == "progress" else {"sent": True}

        connector = Connector()
        host.connectors[identity] = connector
        message = {
            "id": "first",
            "sender": "fixture-user",
            "conversation": "fixture-chat",
            "kind": "direct",
            "text": "Fixture question",
        }
        await host.inbound(identity, message)
        await asyncio.gather(*host.tasks.values())
        assert any(method == "progress" for method, p in connector.calls)
        assert connector.calls[-1][0] == "send" and connector.calls[-1][1]["card"]["enabled"]
        mode = "failure"
        connector.calls.clear()
        await host.inbound(identity, {**message, "id": "second"})
        await asyncio.gather(*host.tasks.values())
        assert any(p.get("phase") == "failed" for method, p in connector.calls)
        assert not any(method == "send" for method, p in connector.calls)
        mode = "cancel"
        connector.calls.clear()
        running.clear()
        await host.inbound(identity, {**message, "id": "third"})
        await running.wait()
        tasks = list(host.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert any(p.get("phase") == "stopped" for method, p in connector.calls)
        assert not host.reply_cards

    asyncio.run(scenario())
