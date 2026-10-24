"""Hard deletion of an idle thread; never removes workspace or shared memories."""

import os
import sqlite3
from contextlib import closing, suppress
from pathlib import Path

from corki.protocol.ids import ThreadId
from corki.storage.sqlite import _joined_write
from corki.storage.thread_writer import ThreadWriterLease
from corki.storage.transcripts import transcript_name

# Child rows precede their referenced items/parent. Unknown future foreign keys
# fail closed instead of silently orphaning data.
_TABLES = (
    "partial_model_items",
    "model_failures",
    "tool_executions",
    "model_steps",
    "messages",
    "conversation_items",
    "hook_executions",
    "hook_batches",
    "transcript_pending",
    "transcript_threads",
    "thread_model_settings",
    "thread_base_instructions",
    "thread_forks",
    "inherited_context_usage",
    "memory_stage1_outputs",
    "turns",
)


class SQLiteThreadDeleteStore:
    def __init__(self, database_path: Path):
        self.path = database_path.resolve()

    async def delete(self, thread_id: ThreadId, *, inactive_before: str | None = None, before_delete=None):
        writer = ThreadWriterLease(self.path, thread_id)
        await writer.acquire()
        try:
            return await _joined_write(self._delete, str(thread_id), inactive_before, before_delete)
        finally:
            await writer.aclose()

    def _delete(self, thread, inactive_before=None, before_delete=None):
        with (
            closing(sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=10)) as db,
            db,
        ):
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA secure_delete=ON")
            db.execute("BEGIN IMMEDIATE")
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            turns = [r[0] for r in db.execute("SELECT id FROM turns WHERE thread_id=?", (thread,))]
            if inactive_before is not None:
                # Recheck under both writer ownership and the write transaction.
                # A candidate may have received a reply since the retention scan.
                eligible = db.execute(
                    "SELECT 1 FROM threads t WHERE t.id=? "
                    "AND julianday(t.updated_at)<julianday(?) "
                    "AND NOT EXISTS(SELECT 1 FROM turns WHERE thread_id=t.id "
                    "AND (status IN ('created','running') OR julianday(updated_at)>=julianday(?))) "
                    "AND NOT EXISTS(SELECT 1 FROM messages WHERE thread_id=t.id "
                    "AND julianday(created_at)>=julianday(?)) "
                    "AND NOT EXISTS(SELECT 1 FROM conversation_items WHERE thread_id=t.id "
                    "AND julianday(created_at)>=julianday(?))",
                    (thread, inactive_before, inactive_before, inactive_before, inactive_before),
                ).fetchone()
                if not eligible:
                    return False
            if before_delete is not None:
                before_delete(thread)
            # Clean ancillary stores first. Keep the canonical thread until every
            # cleanup succeeds, so interrupted deletion can safely be retried.
            self._ancillary(
                "checkpoints.db",
                ("writes", "checkpoints"),
                [thread, *(f"{thread}:{turn}" for turn in turns)],
            )
            self._ancillary(
                "goals_1.sqlite", ("goal_usage", "goal_deferrals", "thread_goals"), [thread]
            )
            self._ancillary(self.path.stem + ".history-notes.db", ("notes",), [thread])
            root = self.path.with_name(self.path.name + ".transcripts")
            if root.exists() or root.is_symlink():
                if os.name == "nt":
                    from corki.platforms.windows_transcripts import delete_transcript

                    delete_transcript(root, transcript_name(thread))
                else:
                    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        with suppress(FileNotFoundError):
                            os.unlink(transcript_name(thread), dir_fd=descriptor)
                    finally:
                        os.close(descriptor)
            for table in _TABLES:
                if table in tables:
                    db.execute(f'DELETE FROM "{table}" WHERE thread_id=?', (thread,))
            if "memory_jobs" in tables:
                db.execute(
                    "DELETE FROM memory_jobs WHERE kind='memory_stage1' AND job_key=?", (thread,)
                )
            db.execute("DELETE FROM threads WHERE id=?", (thread,))
            return True

    def _ancillary(self, name, tables, identities):
        path = self.path.with_name(name)
        if path.is_symlink():
            raise ValueError("会话数据文件不能是符号链接")
        if not path.exists():
            return
        with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=10)) as db, db:
            db.execute("PRAGMA secure_delete=ON")
            db.execute("BEGIN IMMEDIATE")
            existing = {
                r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            for table in tables:
                if table in existing:
                    db.executemany(
                        f'DELETE FROM "{table}" WHERE thread_id=?', ((i,) for i in identities)
                    )
