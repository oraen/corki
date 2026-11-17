"""Small durable child identities; transcripts and model contexts stay in session storage.

Like Codex's spawn graph, an open edge is not evidence of a live worker. Restoring
an identity never starts a model request or replays a previously accepted task.
"""

import sqlite3
from contextlib import asynccontextmanager, closing
from pathlib import Path

from corki.protocol.session_source import SessionSource, ThreadSpawnSource
from corki.storage.sqlite import _joined_write
from corki.storage.thread_writer import ThreadWriterLease


def install_agent_schema(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS agent_children (
            child_id TEXT PRIMARY KEY,
            parent_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('open','closed')),
            status TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_agent_children_parent
            ON agent_children(parent_id,state);
    """)
    migration = "agent_children_v1"
    if db.execute("SELECT 1 FROM schema_migrations WHERE name=?", (migration,)).fetchone():
        return
    # One-time recovery uses typed provenance, never names, model text or guessed IDs.
    # Legacy history has no explicit-close tombstones; recovered identities remain
    # unloaded. In particular, no old input is automatically executed.
    for child, raw_source in db.execute("SELECT id,source FROM threads"):
        source = SessionSource.from_storage(raw_source)
        payload = getattr(source.value, "value", None)
        if isinstance(payload, ThreadSpawnSource) and payload.parent_thread_id != child:
            db.execute(
                "INSERT OR IGNORE INTO agent_children(child_id,parent_id,state,status) "
                "VALUES (?,?,'open','suspended')",
                (child, str(payload.parent_thread_id)),
            )
    db.execute("INSERT INTO schema_migrations(name) VALUES (?)", (migration,))


class AgentStore:
    def __init__(self, path: Path, parent_id: str):
        self.path, self.parent_id = path, str(parent_id)

    @asynccontextmanager
    async def unloaded_lease(self, identity):
        # Do not claim an unloaded child is closed while another process owns it.
        lease = ThreadWriterLease(self.path, identity)
        await lease.acquire()
        try:
            yield
        finally:
            await lease.aclose()

    def _connect(self):
        db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    async def restore(self):
        return await _joined_write(self._restore)

    def _restore(self):
        with closing(self._connect()) as db:
            return [dict(row) for row in db.execute(
                "SELECT child_id,status FROM agent_children WHERE parent_id=? AND state='open'",
                (self.parent_id,),
            )]

    async def lookup(self, identity):
        return await _joined_write(self._lookup, identity)

    def _lookup(self, identity):
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT child_id,status,state FROM agent_children WHERE child_id=? AND parent_id=?",
                (identity, self.parent_id),
            ).fetchone()
            return dict(row) if row else None

    async def create(self, identity):
        await _joined_write(self._create, identity)

    def _create(self, identity):
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO agent_children(child_id,parent_id,state,status) "
                "VALUES (?,?,'open','starting')", (identity, self.parent_id),
            )

    async def update(self, identity, status, *, close=False):
        await _joined_write(self._update, identity, status, close)

    async def admit(self, identity):
        await _joined_write(self._admit, identity)

    def _admit(self, identity):
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state FROM agent_children WHERE child_id=? AND parent_id=?",
                (identity, self.parent_id),
            ).fetchone()
            if not row or row["state"] != "open":
                raise ValueError("Child agent is unknown or explicitly closed")
            thread = db.execute(
                "SELECT source,archived_at FROM threads WHERE id=?", (identity,)
            ).fetchone()
            if thread:
                source = SessionSource.from_storage(thread["source"])
                payload = getattr(source.value, "value", None)
                if (not isinstance(payload, ThreadSpawnSource)
                        or str(payload.parent_thread_id) != self.parent_id):
                    raise ValueError("Stored child ancestry does not match this parent")
                if thread["archived_at"] is not None:
                    raise ValueError("Child session is archived; restore it before sending input")
            db.execute(
                "UPDATE agent_children SET status='starting',updated_at=CURRENT_TIMESTAMP "
                "WHERE child_id=? AND parent_id=?", (identity, self.parent_id),
            )

    def _update(self, identity, status, close):
        with closing(self._connect()) as db, db:
            # A late terminal callback must not reopen an explicitly closed edge.
            db.execute(
                "UPDATE agent_children SET status=?,state=CASE WHEN ? THEN 'closed' ELSE state END,"
                "updated_at=CURRENT_TIMESTAMP WHERE child_id=? AND parent_id=?",
                (status, close, identity, self.parent_id),
            )

    async def result(self, identity):
        return await _joined_write(self._result, identity)

    def _result(self, identity):
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT status,final_answer,error FROM turns WHERE thread_id=? "
                "ORDER BY rowid DESC LIMIT 1", (identity,),
            ).fetchone()
            return dict(row) if row else None
