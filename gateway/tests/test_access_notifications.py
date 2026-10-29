"""Notices use channel transport only, with durable decisions and no duplicate sends."""

import asyncio

from corki_gateway.access import REPLY_DEFAULTS
from corki_gateway.service import Host
from corki_gateway.store import Store


def account(store, **values):
    channel = store.save_connection(
        {
            "channel": "telegram",
            "name": "Notice fixture",
            "values": {"token": "fixture-token", "session_validation": "true", **values},
        }
    )
    store.enable(channel, True)
    return channel


def message(identity="first", **extra):
    return {
        "id": identity,
        "sender": "alice",
        "conversation": "private-room",
        "kind": "direct",
        "text": "hello",
        "name": "Alice",
        **extra,
    }


async def flush(host):
    await asyncio.gather(*list(host.access_notifier.tasks.values()))


class Transport:
    def __init__(self):
        self.sent = []
        self.fail = False

    async def call(self, method, params, **_):
        self.sent.append(params)
        if self.fail:
            raise ValueError("fixture-private-credential")
        return {"sent": True}


def test_each_denied_question_deduplicated_default_and_custom_notices_no_agent(tmp_path):
    async def scenario():
        async def no_agent(*_):
            raise AssertionError("approval notices must never use the model")

        h = Host(tmp_path, runner=no_agent, online=False)
        channel = account(h.store)
        transport = Transport()
        h.connectors[channel] = transport
        try:
            await h.inbound(channel, message())
            await flush(h)
            assert transport.sent[0]["text"] == REPLY_DEFAULTS["validation_first_reply"]
            assert transport.sent[0].get("final") is None
            for value in [message(), message("second")]:
                await h.inbound(channel, value)
                await flush(h)
            assert len(transport.sent) == 2
            assert transport.sent[-1]["message"]["id"] == "second"
            await h.change(
                {
                    "action": "save-connection",
                    "id": channel,
                    "name": "Notice fixture",
                    "values": {
                        "validation_approved_reply": "欢迎加入",
                        "validation_disabled_reply": "请联系管理员",
                    },
                }
            )
            for state, expected in [("approved", "欢迎加入"), ("disabled", "请联系管理员")]:
                await h.change(
                    {
                        "action": "application-status",
                        "id": channel,
                        "sender": "alice",
                        "status": state,
                    }
                )
                await flush(h)
                assert transport.sent[-1]["text"] == expected
                assert transport.sent[-1]["message"]["id"] == "second"
                count = len(transport.sent)
                await h.change(
                    {
                        "action": "application-status",
                        "id": channel,
                        "sender": "alice",
                        "status": state,
                    }
                )
                await flush(h)
                assert len(transport.sent) == count
            # Clearing overrides must use the current defaults, not the previous
            # custom message or an older notice already stored in access_delivery.
            await h.change(
                {
                    "action": "save-connection",
                    "id": channel,
                    "name": "Notice fixture",
                    "values": {"validation_approved_reply": "", "validation_disabled_reply": ""},
                }
            )
            for state, key in [
                ("approved", "validation_approved_reply"),
                ("disabled", "validation_disabled_reply"),
            ]:
                await h.change(
                    {
                        "action": "application-status",
                        "id": channel,
                        "sender": "alice",
                        "status": state,
                    }
                )
                await flush(h)
                assert transport.sent[-1]["text"] == REPLY_DEFAULTS[key]
            assert h.store.applications(channel)["requests"][0]["notification_status"] == "sent"
            assert not h.tasks
        finally:
            await h.close()

    asyncio.run(scenario())


def test_offline_decision_supersedes_old_notice_and_failure_does_not_undo_permissions(tmp_path):
    async def scenario():
        s = Store(tmp_path)
        channel = account(s)
        s.allows_message(s.connection(channel), message(context_token="fixture-private-credential"))
        s.set_application_status(channel, "alice", "approved")
        h = Host(tmp_path, online=False)
        t = Transport()
        t.fail = True
        h.connectors[channel] = t
        try:
            h.access_notifier.schedule(channel)
            await flush(h)
            assert (
                len(t.sent) == 1
                and t.sent[0]["text"] == REPLY_DEFAULTS["validation_approved_reply"]
            )
            public = h.store.applications(channel)["requests"][0]
            assert public["status"] == "approved" and public["notification_status"] == "failed"
            assert "fixture-private-credential" not in str(public)
            assert "message" not in public
            h.access_notifier.schedule(channel)
            await flush(h)
            assert len(t.sent) == 1  # Unknown delivery is not blindly retried.
            t.fail = False
            h.store.set_application_status(channel, "alice", "disabled")
            h.access_notifier.schedule(channel)
            await flush(h)
            assert (
                len(t.sent) == 2
                and t.sent[-1]["text"] == REPLY_DEFAULTS["validation_disabled_reply"]
            )
            assert h.store.applications(channel)["requests"][0]["notification_status"] == "sent"
        finally:
            await h.close()

    asyncio.run(scenario())


def test_slow_transport_does_not_block_admission_or_admin_and_shutdown_closes_it(tmp_path):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        class Slow:
            async def call(self, *_args, **_kwargs):
                started.set()
                await release.wait()
                return {"sent": True}

        h = Host(tmp_path, online=False)
        channel = account(h.store)
        h.connectors[channel] = Slow()
        try:
            async with asyncio.timeout(1):
                result = await h.inbound(channel, message())
                assert result["access_denied"]
                await started.wait()
                await h.change(
                    {
                        "action": "application-status",
                        "id": channel,
                        "sender": "alice",
                        "status": "disabled",
                    }
                )
            assert not release.is_set()
            # Old in-flight completion must not overwrite the newer decision's notice.
            release.set()
            await flush(h)
            row = h.store.applications(channel)["requests"][0]
            assert row["status"] == "disabled" and row["notification_status"] == "sent"
            release.clear()
            h.store.set_application_status(channel, "alice", "approved")
            h.access_notifier.schedule(channel)
            await asyncio.sleep(0)
            await h.close()
            assert not h.access_notifier.tasks
            assert h.store.applications(channel)["requests"][0]["notification_status"] == "failed"
        finally:
            release.set()
            await h.close()

    asyncio.run(scenario())


def test_pending_can_be_rejected_and_notification_recovery_never_replays_uncertain_send(tmp_path):
    store = Store(tmp_path)
    channel = account(store)
    store.allows_message(store.connection(channel), message())
    store.set_application_status(channel, "alice", "disabled")
    notice = store.claim_access_notice(channel)
    assert notice["kind"] == "disabled"
    assert notice["text"] == REPLY_DEFAULTS["validation_disabled_reply"]
    recovered = Store(tmp_path)
    recovered.recover_access_notices()
    assert recovered.claim_access_notice(channel) is None
    assert recovered.applications(channel)["requests"][0]["notification_status"] == "failed"


def test_denied_burst_keeps_each_reply_context_and_disabled_users_get_refusal(tmp_path):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        class Slow(Transport):
            async def call(self, method, params, **kwargs):
                self.sent.append(params)
                started.set()
                await release.wait()
                return {"sent": True}

        h = Host(tmp_path, runner=lambda *_: None, online=False)
        channel = account(
            h.store, validation_first_reply="请等待授权", validation_disabled_reply="权限已关闭"
        )
        transport = Slow()
        h.connectors[channel] = transport
        try:
            await h.inbound(channel, message("one", context_token="context-one"))
            await started.wait()
            for identity in ("two", "three", "three"):
                await h.inbound(channel, message(identity, context_token="context-" + identity))
            release.set()
            await flush(h)
            assert [n["message"]["id"] for n in transport.sent] == ["one", "two", "three"]
            assert [n["message"]["context_token"] for n in transport.sent] == [
                "context-one",
                "context-two",
                "context-three",
            ]
            assert all(n["text"] == "请等待授权" for n in transport.sent)
            await h.change(
                {
                    "action": "application-status",
                    "id": channel,
                    "sender": "alice",
                    "status": "disabled",
                }
            )
            await flush(h)
            assert transport.sent[-1]["text"] == "权限已关闭"
            await h.inbound(channel, message("four"))
            await flush(h)
            assert transport.sent[-1]["text"] == "请等待授权"
            assert transport.sent[-1]["message"]["id"] == "four"
            with h.store.db() as db:
                assert db.execute("SELECT COUNT(*) FROM access_notice_queue").fetchone()[0] == 0
            assert not h.tasks
        finally:
            release.set()
            await h.close()

    asyncio.run(scenario())
