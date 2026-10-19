"""Desktop-only durable metadata; canonical Agent history stays in Corki."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path


class DesktopState:
    def __init__(self, home: Path):
        root = home / "app"
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / "desktop.db"
        with closing(self.connect()) as db, db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS interrupted_messages (
                  thread_id TEXT NOT NULL, turn_id TEXT NOT NULL, message_id TEXT NOT NULL,
                  ordinal INTEGER NOT NULL, content TEXT NOT NULL,
                  PRIMARY KEY(thread_id,turn_id,message_id));
                CREATE TABLE IF NOT EXISTS deleted_threads (id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS metadata (id TEXT PRIMARY KEY, title TEXT);
                CREATE TABLE IF NOT EXISTS values_store (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests (
                  id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, digest TEXT NOT NULL,
                  state TEXT NOT NULL, turn_id TEXT, error TEXT,
                  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE INDEX IF NOT EXISTS requests_by_thread ON requests(thread_id);
            """)
        self.path.chmod(0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def get(self, key, default=None):
        with closing(self.connect()) as db:
            row = db.execute("SELECT value FROM values_store WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def put(self, key, value):
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT INTO values_store VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def request(self, identity):
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM requests WHERE id=?", (identity,)).fetchone()
            return dict(row) if row else None

    def latest_request(self, thread):
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT id,state,turn_id FROM requests WHERE thread_id=? "
                "ORDER BY rowid DESC LIMIT 1",
                (thread,),
            ).fetchone()
            return dict(row) if row else None

    def admit(self, identity, thread, digest):
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT INTO requests(id,thread_id,digest,state) VALUES (?,?,?,?)",
                (identity, thread, digest, "accepted"),
            )

    def update_request(self, identity, state, turn=None, error=None):
        with closing(self.connect()) as db, db:
            db.execute(
                "UPDATE requests SET state=?,turn_id=COALESCE(?,turn_id),error=? WHERE id=?",
                (state, turn, error, identity),
            )

    def unresolved(self):
        with closing(self.connect()) as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM requests WHERE state IN "
                    "('accepted','running','waiting','stopping')"
                )
            ]

    def rename(self, thread, title):
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT INTO metadata VALUES (?,?) "
                "ON CONFLICT(id) DO UPDATE SET title=excluded.title",
                (thread, title),
            )

    def titles(self):
        with closing(self.connect()) as db:
            return dict(db.execute("SELECT id,title FROM metadata"))

    def is_deleted(self, thread):
        with closing(self.connect()) as db:
            return (
                db.execute("SELECT 1 FROM deleted_threads WHERE id=?", (thread,)).fetchone()
                is not None
            )

    def delete_thread(self, thread):
        with closing(self.connect()) as db, db:
            db.execute("PRAGMA secure_delete=ON")
            db.execute("INSERT OR IGNORE INTO deleted_threads VALUES (?)", (thread,))
            db.execute("DELETE FROM metadata WHERE id=?", (thread,))
            db.execute("DELETE FROM requests WHERE thread_id=?", (thread,))
            db.execute("DELETE FROM interrupted_messages WHERE thread_id=?", (thread,))
            db.execute("DELETE FROM values_store WHERE key=?", ("draft:" + thread,))
            row = db.execute("SELECT value FROM values_store WHERE key='ui'").fetchone()
            if row:
                value = json.loads(row[0])
                if isinstance(value, dict) and value.get("selected") == thread:
                    value["selected"] = "new"
                    db.execute(
                        "UPDATE values_store SET value=? WHERE key='ui'", (json.dumps(value),)
                    )

    def save_interrupted(self, thread, turn, messages):
        """Display-only partials, never read by the Agent's model history."""
        with closing(self.connect()) as db, db:
            db.executemany(
                "INSERT OR REPLACE INTO interrupted_messages VALUES (?,?,?,?,?)",
                [
                    (thread, turn, message["id"], index, message["content"])
                    for index, message in enumerate(messages)
                ],
            )

    def interrupted(self, thread, turns, limit):
        if not turns:
            return []
        placeholders = ",".join("?" for _ in turns)
        with closing(self.connect()) as db:
            return [
                {
                    "id": row[0],
                    "sourceId": row[0],
                    "threadId": thread,
                    "turnId": row[3],
                    "role": "assistant",
                    "content": row[1],
                    "truncated": row[2] > limit,
                    "nextText": limit if row[2] > limit else None,
                }
                for row in db.execute(
                    "SELECT message_id,substr(content,1,?),length(content),turn_id "
                    "FROM interrupted_messages WHERE thread_id=? "
                    f"AND turn_id IN ({placeholders}) ORDER BY ordinal",
                    (limit, thread, *turns),
                )
            ]

    def interrupted_text(self, thread, message):
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT content FROM interrupted_messages WHERE thread_id=? AND message_id=?",
                (thread, message),
            ).fetchone()
            if row is None:
                raise LookupError("消息不存在")
            return row[0]
