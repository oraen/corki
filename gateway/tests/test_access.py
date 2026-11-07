"""Admission is durable, per-account and enforced before any Agent execution."""

import asyncio
import json

import httpx
import pytest
from corki_gateway.service import Host
from corki_gateway.standard import define
from corki_gateway.standard_connector import StandardConnector
from corki_gateway.store import Store


def message(identity, sender="alice", text="Hello"):
    return {
        "id": identity,
        "sender": sender,
        "name": "User " + sender,
        "conversation": "chat-" + sender,
        "text": text,
        "kind": "direct",
    }


def account(store, name="Test account", **values):
    identity = store.save_connection(
        {
            "channel": "telegram",
            "name": name,
            "values": {"token": "fixture-token", **values},
        }
    )
    store.enable(identity, True)
    return identity


def status(store, channel, identity):
    with store.db() as db:
        return db.execute(
            "SELECT status FROM receipts WHERE channel=? AND id=?", (channel, identity)
        ).fetchone()[0]


async def drain(host):
    await asyncio.gather(*list(host.tasks.values()))
    await asyncio.sleep(0)


class Transport:
    def __init__(self):
        self.sent = []
        self.notices = []

    async def call(self, method, params, **_):
        if params.get("final"):
            self.sent.append(params["message"]["id"])
        else:
            self.notices.append(params)
        return {"sent": True}


def test_default_open_then_approval_rejection_dedup_and_toggle(tmp_path):
    async def scenario():
        calls = []

        async def run(thread, text, *_):
            calls.append(text)
            return text

        host = Host(tmp_path, runner=run, online=False)
        store = host.store
        identity = account(store)
        transport = Transport()
        host.connectors[identity] = transport
        binding = store.connection(identity)["conversation_id"]
        try:
            await host.inbound(identity, message("open", text="open"))
            await drain(host)
            assert calls == ["open"]
            assert store.applications(identity)["requests"][0]["sender_id"] == "alice"
            await host.change(
                {
                    "action": "save-connection",
                    "id": identity,
                    "name": "Test account",
                    "values": {"session_validation": "true"},
                }
            )
            assert host.connectors[identity] is transport
            assert store.connection(identity)["conversation_id"] == binding
            assert (await host.inbound(identity, message("first", text="first")))["access_denied"]
            assert (await host.inbound(identity, message("second", text="second")))["access_denied"]
            assert not host.tasks and calls == ["open"]
            first = store.applications(identity)["requests"][0]
            assert first["status"] == "pending" and first["first_message"] == "first"
            assert Store(tmp_path).applications(identity)["requests"] == [first]
            await host.change(
                {
                    "action": "application-status",
                    "id": identity,
                    "sender": "alice",
                    "status": "approved",
                }
            )
            assert not host.tasks  # Approval never replays old side effects.
            assert (await host.inbound(identity, message("first")))["duplicate"]
            await host.inbound(identity, message("third", text="third"))
            await drain(host)
            assert calls == ["open", "third"]
            await host.change(
                {
                    "action": "application-status",
                    "id": identity,
                    "sender": "alice",
                    "status": "disabled",
                }
            )
            assert (await host.inbound(identity, message("fourth")))["access_denied"]
            store.save_connection(
                {"id": identity, "name": "Test account", "values": {"session_validation": "false"}}
            )
            await host.inbound(identity, message("fifth", text="fifth"))
            await drain(host)
            assert calls == ["open", "third", "fifth"]
            assert store.applications(identity)["requests"][0]["status"] == "disabled"
            store.save_connection(
                {"id": identity, "name": "Test account", "values": {"session_validation": "true"}}
            )
            assert (await host.inbound(identity, message("sixth")))["access_denied"]
            store.set_application_status(identity, "alice", "approved")
            await host.inbound(identity, message("seventh", text="seventh"))
            await drain(host)
            assert calls[-1] == "seventh"
            assert store.applications(identity)["requests"][0]["first_message"] == "first"
        finally:
            await host.close()

    asyncio.run(scenario())


def test_scoping_pagination_full_text_and_cleanup(tmp_path):
    store = Store(tmp_path)
    one = account(store, session_validation="true")
    two = account(
        store, name="Second account", token="second-fixture-token", session_validation="true"
    )
    for i in range(105):
        assert not store.allows_message(store.connection(one), message(str(i), str(i), "X" * 1000))
    page = store.applications(one)
    earlier = store.applications(one, page["before"])
    assert len(page["requests"]) == 100 and len(earlier["requests"]) == 5
    assert earlier["before"] is None
    assert len(page["requests"][0]["first_message"]) == 240
    assert store.application_message(one, "0") == {"text": "X" * 1000}
    with pytest.raises(ValueError):
        store.application_message(two, "0")
    with pytest.raises(ValueError):
        store.set_application_status(two, "0", "approved")
    store.set_application_status(one, "0", "approved")
    assert not store.allows_message(store.connection(two), message("0", "0"))
    store.reset_conversation(one)
    assert store.allows_message(store.connection(one), message("new", "0"))
    for invalid in (True, 0, -1, "100", 2**63):
        with pytest.raises(ValueError):
            store.applications(one, invalid)
    for invalid in (True, "yes", "", "TRUE"):
        with pytest.raises(ValueError):
            store.save_connection(
                {"id": one, "name": "Test account", "values": {"session_validation": invalid}}
            )
    store.remove_connection(one)
    with store.db() as db:
        assert (
            db.execute("SELECT count(*) FROM access_requests WHERE channel=?", (one,)).fetchone()[0]
            == 0
        )
    assert len(store.applications(two)["requests"]) == 1


def test_kick_cancels_only_sender_active_and_queued_work(tmp_path):
    async def scenario():
        started = asyncio.Event()
        seen = []

        async def run(thread, text, *_):
            seen.append(text)
            if text == "hold":
                started.set()
                await asyncio.Event().wait()
            return text

        h = Host(tmp_path, runner=run, online=False)
        identity = account(h.store, session_validation="true")
        transport = Transport()
        h.connectors[identity] = transport
        for sender in ("alice", "bob"):
            await h.inbound(identity, message("apply-" + sender, sender))
            h.store.set_application_status(identity, sender, "approved")
        try:
            await h.inbound(identity, message("1", text="hold"))
            await started.wait()
            await h.inbound(identity, message("2", text="queued-alice"))
            await h.inbound(identity, message("3", "bob", "bob"))
            await h.change(
                {
                    "action": "application-status",
                    "id": identity,
                    "sender": "alice",
                    "status": "disabled",
                }
            )
            await drain(h)
            assert "queued-alice" not in seen and "bob" in seen
            assert transport.sent == ["3"]
            assert (
                status(h.store, identity, "1") == status(h.store, identity, "2") == "access_denied"
            )
            assert status(h.store, identity, "3") == "completed"
            assert not h.task_senders and not h.locks and not h.access_revocations
        finally:
            await h.close()

    asyncio.run(scenario())


def test_enabling_gate_rechecks_already_queued_messages(tmp_path):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def run(thread, text, *_):
            calls.append(text)
            started.set()
            await release.wait()
            return text

        h = Host(tmp_path, runner=run, online=False)
        identity = account(h.store)
        h.connectors[identity] = Transport()
        try:
            await h.inbound(identity, message("1", text="running"))
            await started.wait()
            await h.inbound(identity, message("2", "alice", "queued"))
            await h.change(
                {
                    "action": "save-connection",
                    "id": identity,
                    "name": "Test account",
                    "values": {"session_validation": "true"},
                }
            )
            assert not h.tasks[(identity, "1")].done()
            release.set()
            await drain(h)
            assert calls == ["running"]
            assert status(h.store, identity, "2") == "access_denied"
            assert h.store.applications(identity)["requests"][0]["sender_id"] == "alice"
        finally:
            release.set()
            await h.close()

    asyncio.run(scenario())


def test_standard_http_skips_pending_without_blocking_approved_user(tmp_path):
    async def scenario():
        calls, sent = [], []

        async def run(thread, text, *_):
            calls.append(text)
            return "reply:" + text

        h = Host(tmp_path, runner=run, online=False)
        provider = define(
            h.store,
            {
                "name": "HTTP fixture",
                "pull_url": "http://localhost/pull",
                "reply_url": "http://localhost/reply",
            },
        )
        identity = h.store.connections(provider)[0]["id"]
        h.store.save_connection(
            {"id": identity, "name": "HTTP fixture", "values": {"session_validation": "true"}}
        )
        h.store.enable(identity, True)
        h.store.allows_message(h.store.connection(identity), message("apply-bob", "bob"))
        h.store.set_application_status(identity, "bob", "approved")
        h.store.http_queue.enqueue(identity, {"messages": [message("1"), message("2", "bob")]})

        async def response(request):
            body = json.loads(request.content)
            if request.url.path == "/pull":
                return httpx.Response(200, json={"messages": []})
            if body.get("type") != "notice":
                sent.append(body["message_id"])
            return httpx.Response(
                200,
                json={
                    "received": True,
                    "message_id": body["message_id"],
                    "reply_id": body["reply_id"],
                },
            )

        async def sleep(_):
            await asyncio.sleep(0.005)

        c = StandardConnector(
            h,
            h.store.connection(identity),
            client=httpx.AsyncClient(transport=httpx.MockTransport(response)),
            sleep=sleep,
        )
        h.connectors[identity] = c
        try:
            await c.start()
            async with asyncio.timeout(5):
                while h.store.http_queue.first(identity):
                    await asyncio.sleep(0.005)
            assert h.store.http_queue.get(identity, "1")["phase"] == "skipped"
            assert h.store.http_queue.get(identity, "2")["phase"] == "sent"
            assert calls == ["Hello"] and sent == ["2"]
        finally:
            await h.close()
            await c.close()

    asyncio.run(scenario())


def test_standard_http_kicked_task_does_not_stop_worker(tmp_path):
    async def scenario():
        started = asyncio.Event()
        calls, sent = [], []

        async def run(thread, text, *_):
            calls.append(text)
            if text == "hold":
                started.set()
                await asyncio.Event().wait()
            return text

        h = Host(tmp_path, runner=run, online=False)
        provider = define(
            h.store,
            {
                "name": "HTTP fixture",
                "pull_url": "http://localhost/pull",
                "reply_url": "http://localhost/reply",
            },
        )
        identity = h.store.connections(provider)[0]["id"]
        h.store.save_connection(
            {"id": identity, "name": "HTTP fixture", "values": {"session_validation": "true"}}
        )
        h.store.enable(identity, True)
        for sender in ("alice", "bob"):
            h.store.allows_message(h.store.connection(identity), message("apply-" + sender, sender))
            h.store.set_application_status(identity, sender, "approved")
        h.store.http_queue.enqueue(
            identity, {"messages": [message("1", text="hold"), message("2", "bob")]}
        )

        async def response(request):
            body = json.loads(request.content)
            if request.url.path == "/pull":
                return httpx.Response(200, json={"messages": []})
            if body.get("type") != "notice":
                sent.append(body["message_id"])
            return httpx.Response(
                200,
                json={
                    "received": True,
                    "message_id": body["message_id"],
                    "reply_id": body["reply_id"],
                },
            )

        async def sleep(_):
            await asyncio.sleep(0.005)

        c = StandardConnector(
            h,
            h.store.connection(identity),
            client=httpx.AsyncClient(transport=httpx.MockTransport(response)),
            sleep=sleep,
        )
        h.connectors[identity] = c
        try:
            await c.start()
            await asyncio.wait_for(started.wait(), 5)
            await h.change(
                {
                    "action": "application-status",
                    "id": identity,
                    "sender": "alice",
                    "status": "disabled",
                }
            )
            async with asyncio.timeout(5):
                while h.store.http_queue.first(identity):
                    await asyncio.sleep(0.005)
            assert not c.reader.done()
            assert h.store.http_queue.get(identity, "1")["phase"] == "skipped"
            assert calls == ["hold", "Hello"] and sent == ["2"]
        finally:
            await h.close()
            await c.close()

    asyncio.run(scenario())


def test_dingtalk_application_user_id_is_display_only_and_does_not_expose_reply_credentials(
    tmp_path,
):
    store = Store(tmp_path)
    identity = store.save_connection(
        {
            "channel": "dingtalk",
            "name": "User ID fixture",
            "values": {
                "client_id": "fixture-app",
                "client_secret": "fixture-secret",
                "session_validation": "true",
            },
        }
    )
    store.enable(identity, True)
    incoming = message("first", "encrypted-fixture")
    incoming.update(staff_id="fixture-user-id", session_webhook="fixture-private-webhook")
    store.allows_message(store.connection(identity), incoming)
    row = store.applications(identity)["requests"][0]
    assert row["user_id"] == "fixture-user-id"
    assert row["sender_id"] == "encrypted-fixture"
    assert "fixture-private-webhook" not in str(row)
    store.set_application_status(identity, row["sender_id"], "approved")
    assert store.applications(identity)["requests"][0]["status"] == "approved"
    # DingTalk display never substitutes encrypted senderId for missing staff IDs.
    store.allows_message(store.connection(identity), message("old", "legacy-fixture"))
    assert store.applications(identity)["requests"][0]["user_id"] is None
    for i, blank in enumerate(("", "   ")):
        empty = message(f"empty-{i}", f"empty-sender-{i}")
        empty["staff_id"] = blank
        store.allows_message(store.connection(identity), empty)
        assert store.applications(identity)["requests"][0]["user_id"] is None
    other = account(store, session_validation="true")
    store.allows_message(store.connection(other), message("other", "telegram-fixture"))
    assert store.applications(other)["requests"][0]["user_id"] == "telegram-fixture"


def test_application_filters_search_all_rows_and_combine_with_pagination(tmp_path):
    store = Store(tmp_path)
    identity = account(store, session_validation="true")
    for i in range(115):
        value = message(str(i), str(i))
        value["name"] = "Old Alice" if i < 110 else "Other name"
        store.allows_message(store.connection(identity), value)
    store.set_application_status(identity, "0", "approved")
    approved = store.applications(identity, name="alice", status="approved")
    assert [r["sender_id"] for r in approved["requests"]] == ["0"]
    first = store.applications(identity, name=" ALICE ", status="pending")
    second = store.applications(identity, first["before"], name="alice", status="pending")
    assert len(first["requests"]) == 100 and len(second["requests"]) == 9
    assert second["before"] is None
    assert store.applications(identity, name="%")["requests"] == []
    assert store.applications(identity, name="' OR 1=1 --")["requests"] == []
    assert len(store.applications(identity)["requests"]) == 100
    for fields in ({"name": None}, {"name": "a" * 81}, {"status": "unexpected"}):
        with pytest.raises(ValueError):
            store.applications(identity, **fields)
