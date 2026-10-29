"""Session isolation, mode switching, membership and concurrent delivery."""

import asyncio
import json

import pytest
from corki_gateway.history import history
from corki_gateway.service import Host
from corki_gateway.store import Store


def account(store, name="Fixture"):
    return store.save_connection(
        {"channel": "telegram", "name": name, "values": {"token": "fixture-token"}}
    )


def msg(identity, sender, text="hello"):
    return {
        "id": identity,
        "sender": sender,
        "conversation": sender,
        "name": "User " + sender,
        "text": text,
        "kind": "direct",
    }


def test_modes_keep_individual_and_default_histories_separate(tmp_path):
    store = Store(tmp_path)
    identity = account(store)
    cwd = str(tmp_path / "main/default")
    binding = store.connection(identity)["conversation_id"]
    a = store.admit(identity, msg("a1", "a"), cwd)
    b = store.admit(identity, msg("b1", "b"), cwd)
    assert a != b
    assert store.admit(identity, msg("a2", "a"), cwd) == a
    assert {r["name"] for r in history(store, identity, {}, {})["sessions"]} == {"User a", "User b"}
    store.save_connection(
        {
            "id": identity,
            "name": "Fixture",
            "values": {"session_mode": "shared", "workdir": "", "token": ""},
        }
    )
    assert store.connection(identity)["conversation_id"] == binding
    shared = store.admit(identity, msg("a3", "a"), cwd)
    assert shared not in (a, b)
    assert store.admit(identity, msg("b3", "b"), cwd) == shared
    assert history(store, identity, {}, {})["sessions"] == [{"thread": shared, "name": "默认"}]
    store.save_connection(
        {"id": identity, "name": "Fixture", "values": {"session_mode": "independent"}}
    )
    assert store.admit(identity, msg("a4", "a"), cwd) == a
    assert store.admit(identity, msg("b4", "b"), cwd) == b
    assert Store(tmp_path).admit(identity, msg("a5", "a"), cwd) == a
    other = account(store, "Other")
    foreign = store.admit(other, msg("a1", "a"), cwd)
    with pytest.raises(ValueError, match="不属于"):
        history(store, identity, {"session": foreign}, {})
    assert history(store, identity, {"session": a}, {})["thread"] == a
    store.reset_conversation(identity)
    assert history(store, identity, {}, {})["sessions"] == []
    assert store.admit(identity, msg("a6", "a"), cwd) != a


def test_session_list_pagination_and_legacy_shared_upgrade(tmp_path):
    store = Store(tmp_path)
    identity = account(store)
    for i in range(105):
        store.admit(identity, msg(str(i), str(i)), str(tmp_path / "main/default"))
    first = history(store, identity, {}, {})
    second = history(store, identity, {"sessions_before": first["sessions_before"]}, {})
    assert len(first["sessions"]) == 100 and len(second["sessions"]) == 5
    assert not (
        {r["thread"] for r in first["sessions"]} & {r["thread"] for r in second["sessions"]}
    )
    assert second["sessions_before"] is None
    selected = second["sessions"][0]["thread"]
    assert history(store, identity, {"session": selected}, {})["thread"] == selected
    with store.db() as db:
        db.execute("DELETE FROM gateway_meta WHERE key='session-modes-v1'")
        db.execute(
            "UPDATE connections SET config=? WHERE id=?", ('{"token":"fixture-token"}', identity)
        )
    migrated = Store(tmp_path)
    assert migrated.connection(identity)["config"]["session_mode"] == "shared"
    assert (
        migrated.connection(identity)["conversation_id"]
        == store.connection(identity)["conversation_id"]
    )


def test_mode_switch_does_not_cancel_accepted_work_and_replies_keep_their_recipient(tmp_path):
    async def scenario():
        started_a = asyncio.Event()
        started_shared = asyncio.Event()
        release_a = asyncio.Event()
        release_shared = asyncio.Event()
        seen = []
        sent = []

        async def runner(thread, text, *_):
            seen.append(text)
            if text == "hold-a":
                started_a.set()
                await release_a.wait()
            if text == "hold-shared":
                started_shared.set()
                await release_shared.wait()
            return "reply " + text

        class Transport:
            async def call(self, method, params, **_):
                sent.append((params["message"]["sender"], params["text"]))
                return {"sent": True}

        host = Host(tmp_path, runner=runner, online=False)
        (tmp_path / "settings.json").write_text(json.dumps({
            "providers": {"fixture": {"models": {"model-a": {}}}},
        }))
        identity = account(host.store)
        host.store.enable(identity, True)
        host.connectors[identity] = Transport()
        try:
            a = await host.inbound(identity, msg("1", "a", "hold-a"))
            await started_a.wait()
            await host.inbound(identity, msg("2", "b", "parallel-b"))
            await asyncio.sleep(0.02)
            assert "parallel-b" in seen and not release_a.is_set()
            await host.change(
                {
                    "action": "save-connection",
                    "id": identity,
                    "name": "Fixture",
                    "values": {"session_mode": "shared", "workdir": "", "token": "",
                               "agent_provider": "fixture", "agent_model": "model-a"},
                }
            )
            assert not host.tasks[(identity, "1")].done()
            c = await host.inbound(identity, msg("3", "c", "hold-shared"))
            await started_shared.wait()
            d = await host.inbound(identity, msg("4", "d", "queued-d"))
            assert c["thread"] == d["thread"] != a["thread"]
            await asyncio.sleep(0.02)
            assert "queued-d" not in seen
            await host.change(
                {
                    "action": "save-connection",
                    "id": identity,
                    "name": "Fixture",
                    "values": {"session_mode": "independent"},
                }
            )
            again = await host.inbound(identity, msg("5", "a", "next-a"))
            assert again["thread"] == a["thread"]
            release_shared.set()
            release_a.set()
            await asyncio.gather(*list(host.tasks.values()))
            assert sorted(sent) == sorted(
                (user, "reply " + text)
                for user, text in (
                    ("a", "hold-a"),
                    ("b", "parallel-b"),
                    ("c", "hold-shared"),
                    ("d", "queued-d"),
                    ("a", "next-a"),
                )
            )
            assert len(seen) == 5
        finally:
            release_a.set()
            release_shared.set()
            await host.close()

    asyncio.run(scenario())
