"""Persist source IDs locally without changing routing or message deduplication."""
from corki_gateway.store import Store


def test_route_ids_migrate_and_backfill_without_changing_thread(tmp_path):
    store = Store(tmp_path)
    identity = store.save_connection({"channel": "dingtalk", "name": "Fixture", "values": {}})
    message = {
        "id": "message-1", "sender": "sender-fixture", "name": "Fixture User",
        "conversation": "conversation-fixture", "text": "not stored in routes",
        "session_webhook": "not stored in routes either",
    }
    cwd = str(tmp_path / "main/default")
    thread = store.admit(identity, message, cwd)
    with store.db() as db:
        original = dict(db.execute("SELECT * FROM routes").fetchone())
        # Reproduce an existing installation's three-column schema and data.
        db.executescript("""
            ALTER TABLE routes RENAME TO old_routes;
            CREATE TABLE routes(key TEXT PRIMARY KEY,thread TEXT NOT NULL,channel TEXT NOT NULL);
            INSERT INTO routes SELECT key,thread,channel FROM old_routes;
            DROP TABLE old_routes;
        """)
    upgraded = Store(tmp_path)
    with upgraded.db() as db:
        row = dict(db.execute("SELECT * FROM routes").fetchone())
        assert row["external_conversation_id"] is None
        assert row["key"] == original["key"] and row["thread"] == thread
    assert upgraded.admit(identity, {**message, "id": "message-2"}, cwd) == thread
    with upgraded.db() as db:
        row = dict(db.execute("SELECT * FROM routes").fetchone())
        assert row["sender_id"] == message["sender"]
        assert row["sender_name"] == message["name"]
        assert row["external_conversation_id"] == message["conversation"]
        assert row["binding_id"] == upgraded.connection(identity)["conversation_id"]
        assert row["workdir"] == cwd and row["last_seen"] > 0
        assert row["key"] == original["key"] and row["thread"] == thread
        assert not any("not stored" in str(value) for value in row.values())
    assert upgraded.admit(identity, {**message, "id": "message-2"}, cwd) is None
    # New nicknames update the diagnostic label, never create another user context.
    renamed = {**message, "id": "message-3", "name": "Renamed"}
    assert upgraded.admit(identity, renamed, cwd) == thread
    persisted = Store(tmp_path)
    with persisted.db() as db:
        assert db.execute("SELECT sender_name FROM routes").fetchone()[0] == "Renamed"
        assert db.execute("SELECT COUNT(*) FROM routes").fetchone()[0] == 1
    persisted.remove_connection(identity)
    with persisted.db() as db:
        assert db.execute("SELECT COUNT(*) FROM routes").fetchone()[0] == 0
