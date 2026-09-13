"""Transactional active/archive collections for the canonical SQLite history."""

import asyncio
import sqlite3
from pathlib import Path

from corki.protocol.ids import ThreadId
from corki.sessions.models import ThreadRecord
from corki.storage.sqlite import _joined_write
from corki.storage.thread_writer import ThreadWriterLease


class SQLiteThreadArchiveStore:
    """Operate on an initialized session database, retaining every canonical row."""

    def __init__(self, database_path: Path) -> None:
        self._path = database_path.resolve()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    async def read(self, thread_id: ThreadId) -> ThreadRecord:
        """Read archived or active metadata without claiming write ownership."""
        return await asyncio.to_thread(self._read, thread_id)

    def _read(self, thread_id: ThreadId) -> ThreadRecord:
        with self._connect() as connection:
            return self._record(connection, thread_id)

    @staticmethod
    def _record(connection: sqlite3.Connection, thread_id: ThreadId) -> ThreadRecord:
        row = connection.execute(
            "SELECT threads.*, EXISTS(SELECT 1 FROM conversation_items "
            "WHERE thread_id=threads.id) AS has_history FROM threads WHERE id=?",
            (str(thread_id),),
        ).fetchone()
        if row is None:
            raise LookupError(f"thread not found: {thread_id}")
        return ThreadRecord(
            ThreadId(row["id"]),
            Path(row["cwd"]),
            row["created_at"],
            row["updated_at"],
            row["archived_at"],
            bool(row["has_history"]),
        )

    async def list_threads(
        self, *, archived: bool = False, cwd: Path | None = None, limit: int = 100
    ) -> tuple[ThreadRecord, ...]:
        """List one bounded history collection without moving or deleting its rows."""
        if not 0 <= limit <= 1000:
            raise ValueError("thread list limit must be between 0 and 1000")
        return await asyncio.to_thread(self._list, archived, cwd, limit)

    def _list(self, archived: bool, cwd: Path | None, limit: int) -> tuple[ThreadRecord, ...]:
        query = "SELECT id FROM threads WHERE archived_at IS " + (
            "NOT NULL" if archived else "NULL"
        )
        parameters: list[str | int] = []
        if cwd is not None:
            query += " AND cwd=?"
            parameters.append(str(cwd.resolve()))
        query += " ORDER BY julianday(updated_at) DESC, id LIMIT ?"
        parameters.append(limit)
        with self._connect() as connection:
            connection.execute("BEGIN")
            rows = connection.execute(query, parameters).fetchall()
            return tuple(self._record(connection, ThreadId(row["id"])) for row in rows)

    async def archive(self, thread_id: ThreadId) -> ThreadRecord:
        """Require an unowned materialized Thread; preserve source recency and memory."""
        return await self._mutate(thread_id, archived=True)

    async def unarchive(self, thread_id: ThreadId) -> ThreadRecord:
        """Restore an unowned archived Thread and refresh its source idle timestamp."""
        return await self._mutate(thread_id, archived=False)

    async def _mutate(self, thread_id: ThreadId, *, archived: bool) -> ThreadRecord:
        writer = ThreadWriterLease(self._path, thread_id)
        await writer.acquire()
        try:
            return await _joined_write(self._write, thread_id, archived)
        finally:
            await writer.aclose()

    def _write(self, thread_id: ThreadId, archived: bool) -> ThreadRecord:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            record = self._record(connection, thread_id)
            if archived:
                if record.archived_at is not None:
                    raise ValueError(f"thread {thread_id} is already archived")
                if not record.has_history:
                    raise ValueError(f"thread {thread_id} has no materialized history")
                connection.execute(
                    "UPDATE threads SET archived_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                    "WHERE id=?",
                    (str(thread_id),),
                )
            else:
                if record.archived_at is None:
                    raise ValueError(f"thread {thread_id} is not archived")
                connection.execute(
                    "UPDATE threads SET archived_at=NULL, "
                    "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id=?",
                    (str(thread_id),),
                )
            return self._record(connection, thread_id)
