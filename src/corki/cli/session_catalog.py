"""Local, bounded session-picker queries; no model or execution ownership.

The caller initializes the canonical repository schema first. Reads use their own
short-lived connections and cancellation joins the worker before returning, so a
cancelled picker cannot leave background reads retaining SQLite resources.
"""

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from corki.protocol.ids import ThreadId
from corki.storage.display_pages import read_display_items_page
from corki.storage.sqlite import _joined_write
from corki.storage.thread_archive import SQLiteThreadArchiveStore


@dataclass(frozen=True)
class SessionRow:
    thread_id: ThreadId
    cwd: str
    updated_at: str
    preview: str
    archived: bool


@dataclass(frozen=True)
class SessionPage:
    rows: tuple[SessionRow, ...]
    has_more: bool


class SessionCatalog:
    def __init__(self, database_path):
        self.path = Path(database_path).resolve()
        self.archive_store = SQLiteThreadArchiveStore(self.path)

    async def page(self, *, cwd=None, archived=False, query="", offset=0, limit=50):
        if not 1 <= limit <= 100 or offset < 0 or len(query) > 1000:
            raise ValueError("invalid session page bounds")
        return await _joined_write(self._page, cwd, archived, query, offset, limit)

    def _connect(self):
        connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        # SQLite's built-in lower() handles ASCII only. Match Codex's Unicode
        # to_lowercase substring search without loading every row into the UI.
        connection.create_function("corki_lower", 1, lambda value: (value or "").lower())
        return connection

    def _page(self, cwd, archived, query, offset, limit):
        where = ["archived_at IS NOT NULL" if archived else "archived_at IS NULL"]
        params = []
        # Empty metadata-only threads cannot be usefully previewed or archived.
        where.append("EXISTS (SELECT 1 FROM conversation_items WHERE thread_id=threads.id)")
        if cwd is not None:
            where.append("cwd=?")
            params.append(str(Path(cwd).resolve()))
        if query:
            where.append(
                "(instr(corki_lower(preview), corki_lower(?)) > 0 OR "
                "instr(corki_lower(cwd), corki_lower(?)) > 0 OR "
                "instr(corki_lower(id), corki_lower(?)) > 0)"
            )
            params.extend([query] * 3)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT id,cwd,updated_at,substr(preview,1,2000) AS preview,archived_at "
                "FROM threads WHERE "
                + " AND ".join(where)
                + " ORDER BY julianday(updated_at) DESC, id LIMIT ? OFFSET ?",
                [*params, limit + 1, offset],
            ).fetchall()
        return SessionPage(
            tuple(
                SessionRow(
                    ThreadId(row["id"]),
                    row["cwd"],
                    row["updated_at"],
                    row["preview"],
                    row["archived_at"] is not None,
                )
                for row in rows[:limit]
            ),
            len(rows) > limit,
        )

    async def preview(self, thread_id, *, limit=30):
        if not 1 <= limit <= 100:
            raise ValueError("invalid preview bounds")
        return await _joined_write(self._preview, thread_id, limit)

    async def transcript_page(self, thread_id, *, cursor=None):
        return await _joined_write(
            read_display_items_page, self._transcript_connection, thread_id, cursor, 20
        )

    def _transcript_connection(self):
        connection = self._connect()
        # Reject oversized SQLite values before fetching/decoding a page into Python.
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 8_000_000)
        return closing(connection)

    def _preview(self, thread_id, limit):
        with closing(self._connect()) as connection:
            connection.execute("BEGIN")
            if (
                connection.execute("SELECT 1 FROM threads WHERE id=?", (str(thread_id),)).fetchone()
                is None
            ):
                raise LookupError("Session no longer exists")
            rows = connection.execute(
                "SELECT kind, substr(json_extract(payload_json, '$.content'),1,4000) AS content "
                "FROM conversation_items WHERE thread_id=? "
                "AND kind IN ('user_message','assistant_message') "
                "ORDER BY sequence DESC LIMIT ?",
                (str(thread_id), limit),
            ).fetchall()
            return tuple((row["kind"], row["content"] or "") for row in reversed(rows))

    async def archive(self, thread_id):
        return await self.archive_store.archive(thread_id)

    async def unarchive(self, thread_id):
        return await self.archive_store.unarchive(thread_id)
