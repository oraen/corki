"""Standard HTTP contract, durable sequencing and reply-only retries."""

import asyncio
import json

import httpx
import pytest
from corki_gateway.service import Host
from corki_gateway.standard import define, resolved
from corki_gateway.standard_connector import StandardConnector
from corki_gateway.store import Store


def definition(store, **changes):
    return define(
        store,
        {
            "name": "Example HTTP",
            "pull_url": "http://localhost/questions",
            "reply_url": "http://localhost/replies",
            **changes,
        },
    )


def test_definition_parameters_and_literal_mode(tmp_path):
    store = Store(tmp_path)
    plain = definition(store)
    assert len(store.connections(plain)) == 1
    assert not store.connections(plain)[0]["enabled"]
    template = definition(
        store,
        name="Template",
        pull_url="http://localhost/p/#{account}?key=#{key}",
        reply_url="https://example.com/r?key=#{key}",
    )
    assert store.connections(template) == []
    with pytest.raises(ValueError, match="参数"):
        store.save_connection({"channel": template, "name": "missing", "values": {}})
    identity = store.save_connection(
        {
            "channel": template,
            "name": "one",
            "values": {"param_account": "用户 /&", "param_key": "a?b#c"},
        }
    )
    urls = resolved(store.connection(identity))
    assert "%E7%94%A8%E6%88%B7%20%2F%26" in urls["pull_url"]
    assert "a%3Fb%23c" in urls["reply_url"]
    assert "a?b#c" not in json.dumps(store.snapshot())
    literal = definition(
        store,
        name="Literal",
        pull_url="http://localhost/p?value=#{arbitrary.name}",
        disable_parameters=True,
    )
    assert len(store.connections(literal)) == 1
    assert "%23%7Barbitrary.name%7D" in resolved(store.connections(literal)[0])["pull_url"]
    disabled = store.save_connection(
        {"channel": template, "name": "disabled", "values": {"http_disable_parameters": "true"}}
    )
    store.enable(disabled, True)
    assert "%23%7Baccount%7D" in resolved(store.connection(disabled))["pull_url"]
    with pytest.raises(ValueError, match="同名"):
        definition(store)


@pytest.mark.parametrize(
    "changes",
    [
        {"pull_url": "file:///tmp/test"},
        {"pull_url": "https://#{host}/pull"},
        {"pull_url": "https://user:pass@example.com/pull"},
        {"retry_count": True},
        {"retry_count": -1},
        {"retry_count": 21},
        {"pull_url": "https://example.com/#{bad.name}"},
    ],
)
def test_invalid_definitions_leave_no_accounts(tmp_path, changes):
    store = Store(tmp_path)
    with pytest.raises(ValueError):
        definition(store, **changes)
    assert store.connections() == []


def test_queue_atomic_cursor_dedup_and_recovery(tmp_path):
    s = Store(tmp_path)
    channel = s.connections(definition(s))[0]["id"]
    q = s.http_queue
    q.enqueue(channel, {"messages": [{"id": "one", "text": "hello"}], "next_cursor": "first"})
    with pytest.raises(ValueError):
        q.enqueue(
            channel,
            {
                "messages": [{"id": "two", "text": "valid"}, {"id": "bad", "text": None}],
                "next_cursor": "invalid",
            },
        )
    assert s.cursor(channel) == "first" and not q.get(channel, "two")
    assert q.enqueue(channel, {"messages": [{"id": "one", "text": "changed"}]}) == 0
    q.update(channel, "one", "running")
    q.recover(channel)
    assert q.first(channel)["phase"] == "blocked"
    q.reply(channel, "one", "saved reply")
    q.attempt(channel, "one")
    q.recover(channel)
    resumed = Store(tmp_path).http_queue.first(channel)
    assert resumed["phase"] == "ready" and resumed["attempts"] == 1
    q.update(channel, "one", "sent")
    assert q.get(channel, "one")["payload"] == "{}" and q.get(channel, "one")["reply"] is None
    assert q.enqueue(channel, {"messages": [{"id": "one", "text": "hello"}]}) == 0


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.005)


def test_worker_batch_reply_ack_order_and_retry_idempotency(tmp_path):
    async def scenario():
        calls, sent, polls, waits = [], [], [], []

        async def runner(thread, text, *_):
            calls.append(text)
            return "answer:" + text

        h = Host(tmp_path, runner=runner)
        identity = h.store.connections(definition(h.store))[0]["id"]
        h.store.enable(identity, True)

        async def response(request):
            body = json.loads(request.content)
            if request.url.path == "/questions":
                polls.append(body)
                if len(polls) == 1:
                    return httpx.Response(
                        200,
                        json={
                            "messages": [
                                {"id": "one", "text": "first"},
                                {"id": "two", "text": "second"},
                            ],
                            "next_cursor": "batch-1",
                        },
                    )
                assert calls == ["first", "second"]
                return httpx.Response(200, json={"messages": []})
            sent.append(body)
            if len(sent) == 1:
                assert calls == ["first"]
                return httpx.Response(
                    200,
                    json={"received": True, "message_id": "WRONG", "reply_id": body["reply_id"]},
                )
            return httpx.Response(
                200,
                json={
                    "received": True,
                    "message_id": body["message_id"],
                    "reply_id": body["reply_id"],
                },
            )

        async def sleep(seconds):
            waits.append(seconds)
            await asyncio.sleep(0.001)

        c = StandardConnector(
            h,
            h.store.connection(identity),
            client=httpx.AsyncClient(transport=httpx.MockTransport(response)),
            sleep=sleep,
        )
        h.connectors[identity] = c
        try:
            await c.start()
            await until(lambda: len(polls) >= 11)
            assert sent[0] == sent[1] and len(sent) == 3
            assert polls[1]["cursor"] == "batch-1"
            assert 5 in waits and waits[0] == 1
            assert all(
                h.store.http_queue.get(identity, i)["phase"] == "sent" for i in ("one", "two")
            )
        finally:
            await h.close()
            await c.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("second_received", [True, False])
def test_exhausted_retries_preserve_answer_and_continue_next_message(tmp_path, second_received):
    async def scenario():
        calls = []
        requests = []

        async def runner(thread, text, *_):
            calls.append(text)
            return text

        h = Host(tmp_path, runner=runner)
        identity = h.store.connections(definition(h.store))[0]["id"]
        h.store.enable(identity, True)
        h.store.http_queue.enqueue(
            identity,
            {"messages": [{"id": "one", "text": "first"}, {"id": "two", "text": "second"}]},
        )

        async def response(request):
            body = json.loads(request.content)
            if request.url.path == "/questions":
                return httpx.Response(200, json={"messages": []})
            requests.append(body)
            return httpx.Response(
                200,
                json={
                    "received": body["message_id"] == "two" and second_received,
                    "reply_id": body["reply_id"],
                    "message_id": body["message_id"],
                },
            )

        async def sleep(_):
            await asyncio.sleep(0.001)

        c = StandardConnector(
            h,
            h.store.connection(identity),
            client=httpx.AsyncClient(transport=httpx.MockTransport(response)),
            sleep=sleep,
        )
        h.connectors[identity] = c
        try:
            await c.start()
            await until(lambda: h.store.http_queue.first(identity) is None)
            assert calls == ["first", "second"]
            assert len(requests) == (5 if second_received else 8)
            assert all(r == requests[0] for r in requests[:4])
            failed = h.store.http_queue.get(identity, "one")
            assert failed["phase"] == "delivery_failed"
            assert json.loads(failed["reply"])["text"] == "first"
            assert h.store.http_queue.get(identity, "two")["phase"] == (
                "sent" if second_received else "delivery_failed"
            )
            assert h.statuses[identity]["state"] == "online"
            rows = h.store.snapshot()["receipts"]
            assert next(r for r in rows if r["id"] == "one")["status"] == "delivery_failed"
            h.store.http_queue.recover(identity)
            assert h.store.http_queue.first(identity) is None
            assert (
                h.store.http_queue.enqueue(identity, {"messages": [{"id": "one", "text": "first"}]})
                == 0
            )
        finally:
            await h.close()
            await c.close()

    asyncio.run(scenario())


def test_restart_resends_saved_reply_without_running_agent(tmp_path):
    async def scenario():
        async def runner(*_):
            raise AssertionError("must not rerun Agent")

        h = Host(tmp_path, runner=runner)
        identity = h.store.connections(definition(h.store))[0]["id"]
        h.store.enable(identity, True)
        q = h.store.http_queue
        q.enqueue(identity, {"messages": [{"id": "one", "text": "question"}]})
        row = q.reply(identity, "one", "saved result")
        saved = json.loads(row["reply"])

        async def response(request):
            if request.url.path == "/questions":
                return httpx.Response(200, json={"messages": []})
            assert json.loads(request.content) == saved
            return httpx.Response(
                200, json={"received": True, "message_id": "one", "reply_id": saved["reply_id"]}
            )

        c = StandardConnector(
            h,
            h.store.connection(identity),
            client=httpx.AsyncClient(transport=httpx.MockTransport(response)),
        )
        h.connectors[identity] = c
        try:
            await c.start()
            await until(lambda: q.first(identity) is None)
        finally:
            await h.close()
            await c.close()

    asyncio.run(scenario())


def test_empty_poll_backoff_recovers_after_new_message(tmp_path):
    async def scenario():
        polls = 0
        waits = []

        async def runner(*_):
            return "reply"

        h = Host(tmp_path, runner=runner)
        identity = h.store.connections(definition(h.store))[0]["id"]
        h.store.enable(identity, True)

        async def response(request):
            nonlocal polls
            body = json.loads(request.content)
            if request.url.path == "/replies":
                return httpx.Response(
                    200,
                    json={
                        "received": True,
                        "message_id": body["message_id"],
                        "reply_id": body["reply_id"],
                    },
                )
            polls += 1
            return httpx.Response(
                200, json={"messages": [{"id": "one", "text": "new"}] if polls == 11 else []}
            )

        async def sleep(seconds):
            waits.append(seconds)
            await asyncio.sleep(0.001)

        c = StandardConnector(
            h,
            h.store.connection(identity),
            client=httpx.AsyncClient(transport=httpx.MockTransport(response)),
            sleep=sleep,
        )
        h.connectors[identity] = c
        try:
            await c.start()
            await until(lambda: len(waits) >= 11)
            assert waits[:11] == [1] * 9 + [5, 1]
        finally:
            await h.close()
            await c.close()

    asyncio.run(scenario())


def test_standard_metadata_save_keeps_conversation_and_reset_discards_pending(tmp_path):
    async def scenario():
        h = Host(tmp_path, online=False)
        identity = h.store.connections(definition(h.store))[0]["id"]
        initial = h.store.connection(identity)["conversation_id"]
        h.store.http_queue.enqueue(identity, {"messages": [{"id": "one", "text": "hello"}]})
        await h.change(
            {
                "action": "save-connection",
                "id": identity,
                "name": "Renamed",
                "values": {"http_disable_parameters": "false", "http_retry_count": "5"},
            }
        )
        assert h.store.connection(identity)["conversation_id"] == initial
        assert h.store.http_queue.first(identity)["phase"] == "queued"
        await h.change({"action": "reset-conversation", "id": identity})
        assert h.store.http_queue.first(identity) is None
        assert h.store.http_queue.get(identity, "one")["phase"] == "skipped"
        assert (
            h.store.http_queue.enqueue(identity, {"messages": [{"id": "one", "text": "hello"}]})
            == 0
        )
        await h.change({"action": "delete-connection", "id": identity})
        assert h.store.http_queue.get(identity, "one") is None

    asyncio.run(scenario())


def test_failed_answer_labels_survive_paging_reset_and_legacy_upgrade(tmp_path):
    from corki_gateway.history import history

    from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
    from corki.storage import SQLiteSessionRepository

    async def scenario():
        h = Host(tmp_path, online=False)
        identity = h.store.connections(definition(h.store))[0]["id"]
        h.store.save_connection(
            {"id": identity, "name": "Reader", "values": {"session_mode": "shared"}}
        )
        thread = next(
            g["thread"]
            for g in h.store.conversation_list()
            if g["id"] == h.store.connection(identity)["conversation_id"]
        )
        repo = SQLiteSessionRepository(tmp_path / "sessions/corki.db")
        await repo.create_thread(thread, tmp_path)
        first = AssistantMessageItem("identical answer", "turn-a", new_step_id())
        second = AssistantMessageItem("identical answer", "turn-b", new_step_id())
        await repo.append_items(
            thread,
            (
                UserMessageItem("question A", "turn-a"),
                first,
                UserMessageItem("question B", "turn-b"),
                second,
            ),
        )
        q = h.store.http_queue
        q.enqueue(identity, {"messages": [{"id": "one", "text": "question A"}]})
        q.bind_answer(identity, "one", thread, first.id)
        q.reply(identity, "one", "identical answer")
        q.update(identity, "one", "delivery_failed")

        def flags(view):
            return [m["delivery_failed"] for m in view["messages"] if m["role"] == "assistant"]

        assert flags(history(h.store, identity, {}, {})) == [True, False]
        assert flags(history(Store(tmp_path), identity, {}, {})) == [True, False]
        await repo.append_items(
            thread, tuple(UserMessageItem(f"Later {i}", "later") for i in range(110))
        )
        latest = history(h.store, identity, {}, {})
        assert latest["before"] is not None
        older = history(h.store, identity, {"before": latest["before"]}, {})
        assert flags(older) == [True, False]
        await h.change({"action": "reset-conversation", "id": identity})
        assert history(h.store, identity, {}, {})["messages"] == []
        assert (
            q.get(identity, "one")["reply"] and q.get(identity, "one")["phase"] == "delivery_failed"
        )
        # Old delivery blocks retain their answer and no longer occupy the queue.
        q.enqueue(identity, {"messages": [{"id": "legacy", "text": "old question"}]})
        h.store.admit(
            identity,
            {"id": "legacy", "text": "old question", "sender": "remote", "conversation": identity},
            str(tmp_path / "main/default"),
        )
        q.reply(identity, "legacy", "old saved reply")
        q.update(identity, "legacy", "blocked")
        q.recover(identity)
        assert q.get(identity, "legacy")["phase"] == "delivery_failed" and q.first(identity) is None
        fallback = history(h.store, identity, {}, {})["messages"]
        assert (
            len(fallback) == 1
            and fallback[0]["content"] == "old saved reply"
            and fallback[0]["delivery_failed"]
        )

    asyncio.run(scenario())
