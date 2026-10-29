"""Account isolation, legacy migration and explicit shared conversation safety."""

import asyncio
import json

import pytest
from corki_gateway.connections import DuplicateAccount
from corki_gateway.service import Host
from corki_gateway.store import Store


def account(store, name, **extra):
    return store.save_connection(
        {"channel": "telegram", "name": name, "values": {"token": "fixture-" + name}, **extra}
    )


def message(identity="same"):
    return {
        "id": identity,
        "sender": "person",
        "conversation": "person",
        "kind": "direct",
        "text": identity,
    }


def test_migration_preserves_old_state_and_route(tmp_path):
    store = Store(tmp_path)
    assert store.snapshot()["connections"] == []
    thread = store.admit("telegram", message(), "/work")
    directory = store.root / "accounts/telegram"
    directory.mkdir(parents=True)
    (directory / "login.json").write_text('{"fixture":true}')
    with store.db() as db:
        db.execute("DELETE FROM gateway_meta WHERE key='connections-v1'")
        db.execute(
            "UPDATE channels SET config=?,enabled=1,account='old-bot' WHERE id='telegram'",
            (json.dumps({"token": "fixture", "workdir": "/work"}),),
        )
    migrated = Store(tmp_path)
    row = migrated.connection("telegram")
    assert row["enabled"] and row["state_key"] == "telegram"
    assert row["config"]["token"] == "fixture" and row["account"] == "old-bot"
    assert migrated.admit("telegram", message("next"), "/work") == thread
    assert (directory / "login.json").read_text() == '{"fixture":true}'
    assert len(Store(tmp_path).connections()) == 1


def test_independent_conversations_per_user_and_directory_reset(tmp_path):
    s = Store(tmp_path)
    a = account(s, "one")
    group = s.connection(a)["conversation_id"]
    b = account(s, "two", conversation_id=group)
    cwd = str(tmp_path / "main/default")
    thread = s.admit(a, message(), cwd)
    assert s.admit(b, message(), cwd) != thread
    assert s.admit(a, {**message("other"), "sender": "someone-else"}, cwd) != thread
    assert s.admit(a, message(), cwd) is None
    with pytest.raises(ValueError, match="同名"):
        account(s, "ONE")
    with pytest.raises(ValueError, match="重置"):
        s.save_connection({"id": a, "name": "one", "values": {"workdir": str(tmp_path)}})
    assert s.connection(a)["conversation_id"] == group
    s.save_connection({"id": a, "name": "renamed", "values": {}})
    assert s.connection(a)["conversation_id"] == group
    s.save_connection(
        {"id": a, "name": "one", "values": {"workdir": str(tmp_path)}, "confirm_reset": True}
    )
    assert s.admit(a, message("new"), str(tmp_path)) != thread
    assert s.connection(b)["conversation_id"] != s.connection(a)["conversation_id"]
    assert "fixture-one" not in json.dumps(s.snapshot())


def test_duplicate_remote_accounts_and_removal_are_isolated(tmp_path):
    s = Store(tmp_path)
    a, b = account(s, "one"), account(s, "two")
    s.enable(a, True)
    s.enable(b, True)
    s.account(a, "remote-user")
    with pytest.raises(DuplicateAccount):
        s.account(b, "remote-user")
    s.enable(a, False)
    s.account(b, "remote-user")
    with pytest.raises(DuplicateAccount):
        s.enable(a, True)
    s.cursor(a, "cursor-a")
    s.cursor(b, "cursor-b")
    s.remove_connection(a)
    assert s.connection(b) and s.cursor(b) == "cursor-b"
    assert not s.cursor(a)


def test_component_uninstall_stops_all_its_accounts_only(tmp_path):
    async def scenario():
        h = Host(tmp_path, online=False)
        a, b = account(h.store, "one"), account(h.store, "two")
        other = h.store.save_connection({"channel": "wechat", "name": "Other", "values": {}})
        for identity in (a, b, other):
            h.store.enable(identity, True)
        stopped = []

        async def stop(identity):
            stopped.append(identity)

        h.stop_channel = stop
        await h.change({"action": "prepare-uninstall", "id": "telegram"})
        assert stopped == [a, b]
        assert h.store.connection(other)["enabled"]
        assert not h.store.connection(a)["enabled"]

    asyncio.run(scenario())


def test_first_message_allowed_reset_stops_queue_and_preserves_other_account(tmp_path):
    async def scenario():
        release = asyncio.Event()

        async def runner(*_):
            await release.wait()
            return "reply"

        class Transport:
            async def call(self, *args, **kwargs):
                return {"sent": True}

        h = Host(tmp_path, runner=runner, online=False)
        a, b = account(h.store, "one"), account(h.store, "two")
        for identity in (a, b):
            h.store.enable(identity, True)
            h.connectors[identity] = Transport()
        first = await h.inbound(a, message())
        await h.inbound(a, message("queued"))
        await h.inbound(b, message())
        await asyncio.sleep(0)
        await h.change({"action": "reset-conversation", "id": a})
        assert not h.tasks[(b, "same")].done()
        rows = h.store.snapshot()["receipts"]
        assert all(r["status"] == "cancelled" for r in rows if r["channel"] == a)
        new = await h.inbound(a, message("new"))
        assert new["thread"] != first["thread"]
        release.set()
        await asyncio.gather(*list(h.tasks.values()))
        await h.change({"action": "channel", "id": a, "enabled": False})
        with pytest.raises(ValueError, match="关闭"):
            await h.inbound(a, message("disabled"))
        with h.store.db() as db:
            assert db.execute("SELECT count(*) FROM peers").fetchone()[0] == 0

    asyncio.run(scenario())


def test_legacy_shared_binding_splits_without_deleting_history(tmp_path):
    s = Store(tmp_path)
    a, b = account(s, "one"), account(s, "two")
    binding = s.connection(a)["conversation_id"]
    with s.db() as db:
        db.execute("UPDATE connections SET conversation_id=? WHERE id=?", (binding, b))
    migrated = Store(tmp_path)
    assert migrated.connection(a)["conversation_id"] == binding
    assert migrated.connection(b)["conversation_id"] != binding
    assert any(g["id"] == binding for g in migrated.conversation_list())
    before = migrated.connection(b)["conversation_id"]
    assert Store(tmp_path).connection(b)["conversation_id"] == before


def test_history_read_does_not_take_mutation_lock(tmp_path):
    from corki_gateway.control import control

    from corki.cron.locking import FileLock

    s = Store(tmp_path)
    identity = account(s, "reader")
    guard = FileLock(s.root / "control.lock")
    assert guard.acquire()
    try:
        view = control(tmp_path, {"action": "conversation", "id": identity})
        assert view["messages"] == []
    finally:
        guard.close()
