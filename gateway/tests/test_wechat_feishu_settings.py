"""Exercise shared settings with each adapter's actual reply context shape."""

import asyncio

import pytest
from corki_gateway.service import Host


@pytest.mark.parametrize("provider", ["wechat", "feishu"])
def test_access_and_unsupported_replies_preserve_channel_context(tmp_path, provider):
    async def scenario():
        calls, sent = [], []

        async def run(_thread, text, *_):
            calls.append(text)
            return "Agent reply"

        class Transport:
            async def call(self, method, params, **_):
                assert method == "send"
                sent.append(params)
                return {"sent": True}

        host = Host(tmp_path, runner=run, online=False)
        credentials = (
            {"app_id": "cli_0123456789abcdef", "app_secret": "fixture"}
            if provider == "feishu"
            else {}
        )
        identity = host.store.save_connection(
            {
                "channel": provider,
                "name": "Settings fixture",
                "values": {
                    **credentials,
                    "session_validation": "true",
                    "validation_first_reply": "Pending fixture",
                    "validation_approved_reply": "Approved fixture",
                    "validation_disabled_reply": "Disabled fixture",
                    "unsupported_reply": "Unsupported fixture",
                },
            }
        )
        host.store.enable(identity, True)
        host.connectors[identity] = Transport()

        def incoming(message_id, **extra):
            return {
                "id": message_id,
                "sender": "fixture-user",
                "conversation": "fixture-user" if provider == "wechat" else "oc_fixture-chat",
                "kind": "direct",
                "text": message_id,
                **({"context_token": "context-" + message_id} if provider == "wechat" else {}),
                **extra,
            }

        async def drain():
            await asyncio.gather(*list(host.tasks.values()))
            await asyncio.gather(*list(host.access_notifier.tasks.values()))

        async def decision(status):
            await host.change(
                {"action": "application-status", "id": identity,
                 "sender": "fixture-user", "status": status}
            )
            await drain()

        try:
            first = incoming("pending", unsupported=True)
            assert (await host.inbound(identity, first))["access_denied"]
            await drain()
            assert calls == []
            assert sent[-1]["text"] == "Pending fixture"
            assert sent[-1]["message"] == first

            await decision("approved")
            assert sent[-1]["text"] == "Approved fixture"
            assert sent[-1]["message"] == first
            assert calls == []  # Approval never replays a denied message.

            await host.inbound(identity, incoming("allowed"))
            await drain()
            assert calls == ["allowed"]

            unsupported = incoming("media", unsupported=True)
            assert (await host.inbound(identity, unsupported))["unsupported"]
            await drain()
            assert sent[-1]["text"] == "Unsupported fixture"
            assert sent[-1]["message"] == unsupported
            assert not sent[-1].get("fallback_proactive")
            count = len(sent)
            assert (await host.inbound(identity, unsupported))["duplicate"]
            await drain()
            assert len(sent) == count and calls == ["allowed"]

            await decision("disabled")
            assert sent[-1]["text"] == "Disabled fixture"
            assert (await host.inbound(identity, incoming("blocked")))["access_denied"]
            await drain()
            assert calls == ["allowed"]

            await host.change(
                {"action": "save-connection", "id": identity, "name": "Settings fixture",
                 "values": {"session_validation": "false"}}
            )
            await host.inbound(identity, incoming("open", sender="another-user"))
            await drain()
            assert calls == ["allowed", "open"]
        finally:
            await host.close()

    asyncio.run(scenario())
