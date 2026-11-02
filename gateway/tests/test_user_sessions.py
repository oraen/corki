"""Per-user context isolation, migration, membership and concurrent delivery."""

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


def test_users_keep_individual_histories_separate(tmp_path):
    store = Store(tmp_path)
    identity = account(store)
    cwd = str(tmp_path / "main/default")
    binding = store.connection(identity)["conversation_id"]
    a = store.admit(identity, msg("a1", "a"), cwd)
    b = store.admit(identity, msg("b1", "b"), cwd)
    assert a != b
    assert store.admit(identity, msg("a2", "a"), cwd) == a
    assert {r["name"] for r in history(store, identity, {}, {})["sessions"]} == {"User a", "User b"}
    assert store.connection(identity)["conversation_id"] == binding
    assert Store(tmp_path).admit(identity, msg("a5", "a"), cwd) == a
    other = account(store, "Other")
    foreign = store.admit(other, msg("a1", "a"), cwd)
    with pytest.raises(ValueError, match="不属于"):
        history(store, identity, {"session": foreign}, {})
    assert history(store, identity, {"session": a}, {})["thread"] == a
    store.reset_conversation(identity)
    assert history(store, identity, {}, {})["sessions"] == []
    assert store.admit(identity, msg("a6", "a"), cwd) != a


def test_session_list_pagination(tmp_path):
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


def test_concurrent_users_and_serial_turns_keep_their_recipient(tmp_path):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        seen, sent = [], []
        async def runner(thread, text, *_):
            seen.append(text)
            if text == "hold-a":
                started.set()
                await release.wait()
            return "reply " + text
        class Transport:
            async def call(self, method, params, **_):
                sent.append((params["message"]["sender"], params["text"]))
                return {"sent": True}
        host = Host(tmp_path, runner=runner, online=False)
        identity = account(host.store)
        host.store.enable(identity, True)
        host.connectors[identity] = Transport()
        try:
            a = await host.inbound(identity, msg("1", "a", "hold-a"))
            await started.wait()
            b = await host.inbound(identity, msg("2", "b", "parallel-b"))
            again = await host.inbound(identity, msg("3", "a", "next-a"))
            await asyncio.sleep(0.02)
            assert b['thread'] != a['thread'] == again['thread']
            assert "parallel-b" in seen and "next-a" not in seen
            release.set()
            await asyncio.gather(*list(host.tasks.values()))
            assert sorted(sent) == sorted([('a','reply hold-a'),('b','reply parallel-b'),('a','reply next-a')])
        finally:
            release.set()
            await host.close()
    asyncio.run(scenario())


def test_retired_setting_migration_never_reuses_mixed_context(tmp_path):
    store = Store(tmp_path)
    identity = account(store)
    cwd = str(tmp_path / 'main/default')
    binding = store.connection(identity)['conversation_id']
    own = store.admit(identity, msg('a1','a'), cwd)
    with store.db() as db:
        mixed = db.execute('SELECT thread FROM conversations WHERE id=?',(binding,)).fetchone()[0]
        db.execute("DELETE FROM gateway_meta WHERE key='user-sessions-only-v1'")
        db.execute('UPDATE connections SET config=? WHERE id=?',
                   (json.dumps({'token':'fixture-token','session_mode':'shared'}), identity))
        db.execute('INSERT INTO user_sessions VALUES(?,?,?,?)',(binding,'b','User b',mixed))
    migrated = Store(tmp_path)
    assert 'session_mode' not in migrated.connection(identity)['config']
    assert 'session_mode' not in json.dumps(migrated.snapshot())
    assert migrated.admit(identity, msg('a2','a'), cwd) == own
    b = migrated.admit(identity, msg('b2','b'), cwd)
    assert b not in (mixed, own)
    assert Store(tmp_path).admit(identity, msg('b3','b'), cwd) == b
    with migrated.db() as db:
        assert db.execute('SELECT thread FROM conversations WHERE id=?',(binding,)).fetchone()[0] == mixed
    with pytest.raises(ValueError, match='未知'):
        migrated.save_connection({'id':identity,'name':'Fixture','values':{'session_mode':'shared'}})
