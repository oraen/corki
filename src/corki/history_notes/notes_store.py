"""Thread-isolated virtual notes, with transactional append and no host path access."""

import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from corki.history_notes.output import bounded_records, bounded_text

_ROOT = "/root/notes"
_MAX_BYTES = 1_000_000


def virtual_path(value: str | None, *, prefix: bool = False) -> str:
    if value is None and prefix:
        return _ROOT + "/"
    if (
        not isinstance(value, str)
        or not value
        or "\0" in value
        or len(value.encode("utf-8")) > 4096
    ):
        raise ValueError("Note path must contain 1 to 4096 UTF-8 bytes")
    if value in (_ROOT, _ROOT + "/") and prefix:
        return _ROOT + "/"
    relative = value[len(_ROOT) + 1 :] if value.startswith(_ROOT + "/") else value
    if value.startswith("/") and not value.startswith(_ROOT + "/"):
        raise ValueError("Local notes support only the current /root agent")
    if prefix and relative.endswith("/"):
        relative = relative[:-1]
    if any(part in ("", ".", "..") for part in relative.split("/")):
        raise ValueError("Empty, '.' and '..' note path components are unsupported")
    return _ROOT + "/" + relative + ("/" if prefix and value.endswith("/") else "")


class NotesStore:
    """Open a short-lived connection per operation; mutations use one write transaction."""

    def __init__(self, path: Path, thread_id: str) -> None:
        self.path, self.thread_id = path, str(thread_id)

    def call(self, action: str, arguments: dict, budget: int) -> dict:
        """Execute on a joined worker, never on the Runtime event loop."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(descriptor)
        with closing(sqlite3.connect(self.path, timeout=10)) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.execute(
                "CREATE TABLE IF NOT EXISTS notes (thread_id TEXT NOT NULL, path TEXT NOT NULL, "
                "text TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, "
                "PRIMARY KEY(thread_id,path))"
            )
            if action in ("write_file", "append_to_file"):
                return self._write(connection, action, arguments)
            if action == "read_file":
                return self._read(connection, arguments, budget)
            if action == "list_files_by_prefix":
                return self._list(connection, arguments, budget)
            if action == "search_contents":
                return self._search(connection, arguments, budget)
            if action == "thread_hint":
                files = self._list(connection, {"max_results": 20}, 3000)["files"]
                paths = "\n".join(file["path"] for file in files)
                text = (
                    "Local recovery is available after context reset. Use history.list_windows, "
                    "history.search_contents and history.read_item for this thread's archive. "
                    "Use notes tools for persistent rollout notes. Results are readable local "
                    "compatibility output, not decrypted native history."
                )
                if paths:
                    text += "\nSaved note paths:\n" + paths
                return bounded_text({"text": text}, "text", budget)
            raise ValueError("Unknown local notes action")

    def _write(self, connection, action, arguments):
        path, text = virtual_path(arguments["path"]), arguments["text"]
        connection.execute("BEGIN IMMEDIATE")
        previous = connection.execute(
            "SELECT text FROM notes WHERE thread_id=? AND path=?", (self.thread_id, path)
        ).fetchone()
        if previous is not None and action == "append_to_file":
            text = previous["text"] + text
        count = len(text.encode("utf-8"))
        if count > _MAX_BYTES:
            raise ValueError("Note file exceeds 1,000,000 UTF-8 bytes; write was not applied")
        now = time.time_ns()
        connection.execute(
            "INSERT INTO notes VALUES (?,?,?,?,?) ON CONFLICT(thread_id,path) DO UPDATE SET "
            "text=excluded.text, updated_at=excluded.updated_at",
            (self.thread_id, path, text, now, now),
        )
        return {"path": path, "bytes": count}

    def _read(self, connection, arguments, budget):
        path = virtual_path(arguments["path"])
        row = connection.execute(
            "SELECT text FROM notes WHERE thread_id=? AND path=?", (self.thread_id, path)
        ).fetchone()
        if row is None:
            raise ValueError("Note does not exist in this thread")
        lines = row["text"].splitlines(keepends=True)
        start, stop = arguments.get("start_line"), arguments.get("stop_line")
        if start == 0 or stop == 0:
            raise ValueError("Note line numbers are 1-based; zero is invalid")
        start = 1 if start is None else len(lines) + start + 1 if start < 0 else start
        stop = len(lines) if stop is None else len(lines) + stop + 1 if stop < 0 else stop
        start, stop = max(1, start), min(len(lines), stop)
        text = "".join(lines[start - 1 : stop]) if stop >= start else ""
        return bounded_text(
            {
                "path": path,
                "start_line": start,
                "stop_line": stop,
                "text": text,
                "truncated": False,
            },
            "text",
            budget,
        )

    def _rows(self, connection, prefix, order_by, descending, *, text=False):
        if order_by not in ("name", "created_at", "updated_at"):
            raise ValueError("Invalid note ordering")
        column = "path" if order_by == "name" else order_by
        fields = "path, created_at, updated_at" + (", text" if text else "")
        direction = "DESC" if descending else "ASC"
        return connection.execute(
            f"SELECT {fields} FROM notes WHERE thread_id=? AND substr(path,1,?)=? "
            f"ORDER BY {column} {direction}, path ASC",
            (self.thread_id, len(prefix), prefix),
        )

    def _list(self, connection, arguments, budget):
        prefix = virtual_path(arguments.get("prefix"), prefix=True)
        rows = self._rows(
            connection,
            prefix,
            arguments.get("file_order_by", "name"),
            arguments.get("file_order", "ascending") == "descending",
        )
        return bounded_records(
            "files", (dict(row) for row in rows), arguments.get("max_results", 100), budget
        )

    def _search(self, connection, arguments, budget):
        prefix = virtual_path(arguments.get("path_prefix"), prefix=True)
        rows = self._rows(
            connection, prefix, "created_at", arguments.get("recent_file_first", False), text=True
        )

        def matches():
            for row in rows:
                count = 0
                for number, line in enumerate(row["text"].splitlines(keepends=True), 1):
                    if arguments["query"] in line:
                        yield {"path": row["path"], "line": number, "text": line}
                        count += 1
                        if count >= arguments.get("max_matches_per_file", 20):
                            break

        # Limit matching files, not the number of individual matching lines.
        def limited():
            paths = set()
            for match in matches():
                paths.add(match["path"])
                if len(paths) > arguments.get("max_files", 100):
                    yield {"path": match["path"], "line": match["line"], "text": match["text"]}
                    return
                yield match

        result = bounded_records("matches", limited(), budget, budget)
        paths = list(dict.fromkeys(m["path"] for m in result["matches"]))
        if len(paths) > arguments.get("max_files", 100):
            result["matches"] = [m for m in result["matches"] if m["path"] != paths[-1]]
            result["truncated"] = True
        return result
