"""Versioned SQLite repository for threads, turns, items, and tool execution."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from contextlib import AbstractContextManager
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import Any

from corki.models.failure import ModelFailure
from corki.models.types import ModelCompleted, ModelUsage
from corki.prompting.compaction import render_compaction_summary
from corki.protocol.execution_identity import ExecutionIdentity
from corki.protocol.ids import (
    MessageId,
    ModelStepId,
    SessionId,
    ThreadId,
    ToolCallId,
    TurnId,
)
from corki.protocol.items import (
    AssistantMessageItem,
    BudgetNoticeItem,
    CompactionItem,
    ContextItem,
    ContextRole,
    ConversationItem,
    HostedToolItem,
    ReasoningItem,
    ToolCallItem,
    ToolResultItem,
    TurnAbortedItem,
    UserMessageItem,
    item_from_payload,
    item_kind,
    item_to_payload,
    items_from_messages,
)
from corki.protocol.memory import ThreadMemoryMode
from corki.protocol.messages import Message, MessageRole
from corki.protocol.session_source import DEFAULT_SESSION_SOURCE, SessionSource
from corki.protocol.settings import ModelSettingsSnapshot, ThreadModelSettings
from corki.protocol.tools import (
    CodeModeOutput,
    ImageAttachment,
    ToolCall,
    ToolResult,
    ToolStateUpdate,
    content_from_payload,
    content_to_payload,
    tool_spec_from_payload,
)
from corki.protocol.wire_json import loads_wire, materialize
from corki.protocol.wire_numbers import dumps_wire, loads_number_values
from corki.sessions.display import (
    DisplayItemsCursor,
    DisplayItemsPage,
    DisplayTurnsCursor,
    DisplayTurnsPage,
)
from corki.sessions.models import ContextUsage, DisplayHistory, TurnRecord, TurnStatus
from corki.storage.display_pages import (
    contains_display_item,
    display_turn_from_row,
    read_display_items_page,
    read_display_turns_page,
)
from corki.storage.preview import migrate_thread_previews, user_preview


class StorageIntegrityError(RuntimeError):
    """Raised when SQLite reports durable data corruption."""


class SQLiteSessionRepository:
    """Durably append canonical items without blocking the event loop."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._create_schema()
        self._path.chmod(0o600)

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> AbstractContextManager[sqlite3.Connection]:
        connection = sqlite3.connect(self._path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _create_schema(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
            if quick_check != "ok":
                raise StorageIntegrityError(f"SQLite quick_check failed: {quick_check}")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    name TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY,
                    cwd TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS turns (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL REFERENCES threads(id),
                    status TEXT NOT NULL,
                    user_input TEXT NOT NULL,
                    final_answer TEXT,
                    error TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS thread_base_instructions (
                    thread_id TEXT PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE,
                    model TEXT NOT NULL,
                    instructions TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS thread_forks (
                    thread_id TEXT PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE,
                    source_thread_id TEXT NOT NULL,
                    request_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS inherited_context_usage (
                    thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    total_tokens INTEGER,
                    anchor_id TEXT,
                    input_tokens INTEGER,
                    sample_id TEXT,
                    PRIMARY KEY (thread_id, ordinal)
                );
                CREATE TABLE IF NOT EXISTS transcript_threads (
                    thread_id TEXT PRIMARY KEY REFERENCES threads(id)
                );
                CREATE TABLE IF NOT EXISTS transcript_pending (
                    thread_id TEXT PRIMARY KEY REFERENCES threads(id)
                );
                CREATE INDEX IF NOT EXISTS turns_thread_order ON turns(thread_id);
                CREATE TABLE IF NOT EXISTS hook_executions (
                    execution_key TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL REFERENCES threads(id),
                    turn_id TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT
                );
                CREATE TABLE IF NOT EXISTS hook_batches (
                    batch_key TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL REFERENCES threads(id),
                    turn_id TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS hook_executions_scope
                    ON hook_executions(thread_id, turn_id);
                CREATE TABLE IF NOT EXISTS thread_model_settings (
                    thread_id TEXT PRIMARY KEY REFERENCES threads(id),
                    model TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    reasoning_effort TEXT
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL REFERENCES threads(id),
                    turn_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tool_calls_json TEXT NOT NULL,
                    tool_call_id TEXT,
                    name TEXT,
                    attachments_json TEXT NOT NULL,
                    reasoning_content TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_items (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL REFERENCES threads(id),
                    turn_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS conversation_items_thread_sequence
                    ON conversation_items(thread_id, sequence);
                CREATE INDEX IF NOT EXISTS conversation_items_turn
                    ON conversation_items(thread_id, turn_id, sequence);
                CREATE INDEX IF NOT EXISTS conversation_items_compaction_sequence
                    ON conversation_items(thread_id, sequence) WHERE kind='compaction';
                CREATE TRIGGER IF NOT EXISTS transcript_pending_after_item
                AFTER INSERT ON conversation_items
                WHEN EXISTS (SELECT 1 FROM transcript_threads WHERE thread_id=NEW.thread_id)
                BEGIN
                    INSERT OR IGNORE INTO transcript_pending(thread_id) VALUES (NEW.thread_id);
                END;
                CREATE TABLE IF NOT EXISTS model_steps (
                    step_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL REFERENCES threads(id),
                    turn_id TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    cached_tokens INTEGER NOT NULL,
                    reasoning_tokens INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL,
                    end_turn INTEGER CHECK (end_turn IN (0, 1)),
                    step_index INTEGER,
                    items_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS tool_executions (
                    call_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL REFERENCES threads(id),
                    turn_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_sha256 TEXT,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS partial_model_items (
                    thread_id TEXT NOT NULL REFERENCES threads(id),
                    turn_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    item_id TEXT NOT NULL REFERENCES conversation_items(id),
                    ordinal INTEGER NOT NULL,
                    PRIMARY KEY(thread_id, turn_id, step_index, item_id),
                    UNIQUE(thread_id, turn_id, step_index, ordinal)
                );
                CREATE TABLE IF NOT EXISTS model_failures (
                    thread_id TEXT NOT NULL REFERENCES threads(id),
                    turn_id TEXT NOT NULL,
                    step_index INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(thread_id, turn_id, step_index)
                );
                """
            )
            # Serialize legacy column inspection/ALTER with other openers.
            connection.execute("BEGIN IMMEDIATE")
            base_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(thread_base_instructions)")
            }
            if "provenance" not in base_columns:
                connection.execute(
                    "ALTER TABLE thread_base_instructions ADD COLUMN "
                    "provenance TEXT DEFAULT 'model'"
                )
            if not connection.execute(
                "SELECT 1 FROM schema_migrations WHERE name='transcript_pending_v1'"
            ).fetchone():
                connection.execute(
                    "INSERT OR IGNORE INTO transcript_pending "
                    "SELECT thread_id FROM transcript_threads"
                )
                connection.execute(
                    "INSERT INTO schema_migrations(name) VALUES ('transcript_pending_v1')"
                )
            settings_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(thread_model_settings)")
            }
            if "collaboration_mode" not in settings_columns:
                connection.execute(
                    "ALTER TABLE thread_model_settings ADD COLUMN "
                    "collaboration_mode TEXT NOT NULL DEFAULT 'default'"
                )
            if "collaboration_instructions" not in settings_columns:
                connection.execute(
                    "ALTER TABLE thread_model_settings ADD COLUMN collaboration_instructions TEXT"
                )
            if "personality" not in settings_columns:
                connection.execute("ALTER TABLE thread_model_settings ADD COLUMN personality TEXT")
            thread_columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)")}
            if "archived_at" not in thread_columns:
                connection.execute("ALTER TABLE threads ADD COLUMN archived_at TEXT")
            if "memory_mode" not in thread_columns:
                connection.execute(
                    "ALTER TABLE threads ADD COLUMN memory_mode TEXT NOT NULL DEFAULT 'enabled'"
                )
            if "session_id" not in thread_columns:
                connection.execute("ALTER TABLE threads ADD COLUMN session_id TEXT")
                connection.execute("UPDATE threads SET session_id=id")
            if "source" not in thread_columns:
                connection.execute(
                    "ALTER TABLE threads ADD COLUMN source TEXT NOT NULL DEFAULT 'vscode'"
                )
            turn_columns = {row[1] for row in connection.execute("PRAGMA table_info(turns)")}
            if "base_instructions" not in turn_columns:
                connection.execute("ALTER TABLE turns ADD COLUMN base_instructions TEXT")
            if "model_settings_json" not in turn_columns:
                connection.execute("ALTER TABLE turns ADD COLUMN model_settings_json TEXT")
            if "operation" not in turn_columns:
                connection.execute(
                    "ALTER TABLE turns ADD COLUMN operation TEXT NOT NULL DEFAULT 'normal'"
                )
            tool_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(tool_executions)")
            }
            if "arguments_sha256" not in tool_columns:
                connection.execute("ALTER TABLE tool_executions ADD COLUMN arguments_sha256 TEXT")
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(messages)").fetchall()
            }
            if "reasoning_content" not in columns:
                connection.execute("ALTER TABLE messages ADD COLUMN reasoning_content TEXT")
            step_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(model_steps)").fetchall()
            }
            if "step_index" not in step_columns:
                connection.execute("ALTER TABLE model_steps ADD COLUMN step_index INTEGER")
            if "end_turn" not in step_columns:
                connection.execute(
                    "ALTER TABLE model_steps ADD COLUMN end_turn INTEGER CHECK (end_turn IN (0, 1))"
                )
            if "items_json" not in step_columns:
                connection.execute(
                    "ALTER TABLE model_steps ADD COLUMN items_json TEXT NOT NULL DEFAULT '[]'"
                )
            if "total_tokens" not in step_columns:
                connection.execute("ALTER TABLE model_steps ADD COLUMN total_tokens INTEGER")
            if "usage_anchor_id" not in step_columns:
                connection.execute("ALTER TABLE model_steps ADD COLUMN usage_anchor_id TEXT")
            if "usage_details_json" not in step_columns:
                connection.execute(
                    "ALTER TABLE model_steps ADD COLUMN usage_details_json "
                    "TEXT NOT NULL DEFAULT '{}'"
                )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS model_steps_turn_index
                ON model_steps(thread_id, turn_id, step_index)
                WHERE step_index IS NOT NULL
                """
            )
            self._migrate_legacy_messages(connection)
            migrate_thread_previews(connection)

    def _migrate_legacy_messages(self, connection: sqlite3.Connection) -> None:
        migration = "legacy_messages_to_conversation_items_v1"
        if connection.execute(
            "SELECT 1 FROM schema_migrations WHERE name=?", (migration,)
        ).fetchone():
            return
        rows = connection.execute("SELECT * FROM messages ORDER BY thread_id, sequence").fetchall()
        next_sequence: dict[str, int] = {}
        for row in rows:
            thread_id = row["thread_id"]
            sequence = next_sequence.setdefault(thread_id, 0)
            for item in items_from_messages((_legacy_row_to_message(row),)):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO conversation_items(
                        id, thread_id, turn_id, sequence, kind, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(item.id),
                        thread_id,
                        str(item.turn_id),
                        sequence,
                        item_kind(item),
                        json.dumps(item_to_payload(item), ensure_ascii=False),
                        item.created_at,
                    ),
                )
                sequence += 1
            next_sequence[thread_id] = sequence
        connection.execute("INSERT INTO schema_migrations(name) VALUES (?)", (migration,))

    async def thread_exists(self, thread_id: ThreadId) -> bool:
        return await asyncio.to_thread(self._thread_exists, thread_id)

    def _thread_exists(self, thread_id: ThreadId) -> bool:
        with self._connect() as connection:
            return (
                connection.execute("SELECT 1 FROM threads WHERE id=?", (str(thread_id),)).fetchone()
                is not None
            )

    async def create_thread(
        self,
        thread_id: ThreadId,
        cwd: Path,
        *,
        memory_mode: ThreadMemoryMode = ThreadMemoryMode.ENABLED,
        session_id: SessionId | None = None,
        session_source: SessionSource = DEFAULT_SESSION_SOURCE,
    ) -> None:
        """Create the complete initial metadata once, without overwriting resumed settings."""
        if not isinstance(session_source, SessionSource):
            raise ValueError("session source must be host-validated")
        identity = ExecutionIdentity(
            thread_id, SessionId(str(thread_id)) if session_id is None else session_id
        )
        await _joined_write(
            self._create_thread,
            thread_id,
            cwd,
            ThreadMemoryMode(memory_mode),
            identity.session_id,
            session_source,
        )

    def _create_thread(
        self,
        thread_id: ThreadId,
        cwd: Path,
        memory_mode: ThreadMemoryMode,
        session_id: SessionId,
        session_source: SessionSource,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO threads(id, cwd, memory_mode, session_id, source) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    str(thread_id),
                    str(cwd.resolve()),
                    memory_mode.value,
                    str(session_id),
                    session_source.storage_value,
                ),
            )

    async def load_fork_snapshot(self, thread_id):
        from corki.storage.forks import read_fork_snapshot

        def read():
            with self._connect() as connection:
                connection.execute("BEGIN")
                return read_fork_snapshot(connection, thread_id)

        # Join the reader even on cancellation before its borrowed owner can close.
        return await _joined_write(read)

    async def fork_thread(
        self,
        thread_id,
        cwd,
        *,
        memory_mode,
        session_id,
        session_source,
        source_thread_id,
        before_user_message=None,
        snapshot=None,
    ) -> None:
        from corki.storage.forks import publish_fork

        await _joined_write(
            publish_fork,
            self,
            thread_id,
            cwd,
            memory_mode,
            session_id,
            session_source,
            source_thread_id,
            before_user_message,
            snapshot,
        )

    async def load_thread_source(self, thread_id: ThreadId) -> SessionSource:
        """Return the original stored source, not a new Runtime's initiating source."""
        return await asyncio.to_thread(self._load_thread_source, thread_id)

    async def materialize_transcript(self, thread_id: ThreadId) -> Path | None:
        """Create a local history projection and keep it current after future commits."""
        return await _joined_write(self._materialize_transcript, thread_id)

    async def ensure_base_instructions(self, thread_id, model, instructions, provenance="model"):
        """Commit the initial session prefix once, or return its persisted origin."""
        return await _joined_write(
            self._ensure_base_instructions, thread_id, model, instructions, provenance
        )

    def _ensure_base_instructions(self, thread_id, model, instructions, provenance="model"):
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO thread_base_instructions "
                "(thread_id,model,instructions,provenance) VALUES (?, ?, ?, ?)",
                (str(thread_id), model, instructions, provenance),
            )
            row = connection.execute(
                "SELECT model,instructions,provenance FROM thread_base_instructions "
                "WHERE thread_id=?",
                (str(thread_id),),
            ).fetchone()
            return row["model"], row["instructions"], row["provenance"]

    async def transcript_publication_pending(self, thread_id: ThreadId) -> bool:
        """Inspect this thread's durable copy backlog without initiating a retry."""
        # Join the reader on cancellation too. The unbound closure deliberately
        # skips _after_durable_write: observing backlog must not retry publication.
        return await _joined_write(lambda: self._transcript_publication_pending(thread_id))

    def _transcript_publication_pending(self, thread_id):
        with self._connect() as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM transcript_pending WHERE thread_id=?", (str(thread_id),)
                ).fetchone()
                is not None
            )

    def _materialize_transcript(self, thread_id):
        from corki.storage.transcripts import flush_transcript

        with self._connect() as connection:
            if (
                connection.execute("SELECT 1 FROM threads WHERE id=?", (str(thread_id),)).fetchone()
                is None
            ):
                return None
            connection.execute(
                "INSERT OR IGNORE INTO transcript_threads(thread_id) VALUES (?)", (str(thread_id),)
            )
            connection.execute(
                "INSERT OR IGNORE INTO transcript_pending(thread_id) VALUES (?)", (str(thread_id),)
            )
        return flush_transcript(self, thread_id)

    def _after_durable_write(self):
        from corki.storage.transcripts import flush_transcript

        with self._connect() as connection:
            threads = connection.execute("SELECT thread_id FROM transcript_pending").fetchall()
        for row in threads:
            try:
                flush_transcript(self, row[0])
            except (OSError, ValueError, sqlite3.Error):
                # A projection failure cannot undo a committed effect or make
                # callers retry it. Registration survives for a later retry.
                logging.getLogger(__name__).warning(
                    "Local transcript publication failed", exc_info=True
                )

    def _load_thread_source(self, thread_id: ThreadId) -> SessionSource:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT source FROM threads WHERE id=?", (str(thread_id),)
            ).fetchone()
        if row is None:
            raise StorageIntegrityError("source thread does not exist")
        return SessionSource.from_storage(row[0])

    async def load_thread_session_id(self, thread_id: ThreadId) -> SessionId:
        """Read persisted identity; corruption must not silently reassign running work."""
        return await asyncio.to_thread(self._load_thread_session_id, thread_id)

    def _load_thread_session_id(self, thread_id: ThreadId) -> SessionId:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT session_id FROM threads WHERE id=?", (str(thread_id),)
            ).fetchone()
        if row is None:
            raise StorageIntegrityError("execution identity thread does not exist")
        try:
            return ExecutionIdentity(thread_id, row[0]).session_id
        except ValueError as error:
            raise StorageIntegrityError("invalid stored execution identity") from error

    async def save_thread_model_settings(
        self, thread_id: ThreadId, settings: ThreadModelSettings
    ) -> None:
        """Atomically publish defaults; a cancelled caller must still join the write."""
        await _joined_write(self._save_thread_model_settings, thread_id, settings)

    def _save_thread_model_settings(
        self, thread_id: ThreadId, settings: ThreadModelSettings
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO thread_model_settings(thread_id, model, provider, reasoning_effort, "
                "collaboration_mode, collaboration_instructions, personality) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(thread_id) DO UPDATE SET "
                "model=excluded.model, provider=excluded.provider, "
                "reasoning_effort=excluded.reasoning_effort, "
                "collaboration_mode=excluded.collaboration_mode, "
                "collaboration_instructions=excluded.collaboration_instructions, "
                "personality=excluded.personality",
                (
                    str(thread_id),
                    settings.model,
                    settings.provider,
                    settings.reasoning_effort,
                    settings.collaboration_mode,
                    settings.collaboration_instructions,
                    settings.personality,
                ),
            )

    async def load_thread_model_settings(self, thread_id: ThreadId) -> ThreadModelSettings | None:
        """Read defaults without blocking an active runtime loop."""
        return await asyncio.to_thread(self.read_thread_model_settings, thread_id)

    def read_thread_model_settings(self, thread_id: ThreadId) -> ThreadModelSettings | None:
        """Synchronous composition-root read, before async clients are constructed."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT model, provider, reasoning_effort, collaboration_mode, "
                "collaboration_instructions, personality FROM thread_model_settings "
                "WHERE thread_id=?",
                (str(thread_id),),
            ).fetchone()
        if row is None:
            return None
        try:
            return ThreadModelSettings(*row)
        except ValueError as error:
            raise StorageIntegrityError("invalid stored thread model settings") from error

    async def latest_thread(self, cwd: Path | None = None) -> ThreadId | None:
        return await asyncio.to_thread(self._latest_thread, cwd)

    async def set_thread_memory_mode(self, thread_id: ThreadId, mode: ThreadMemoryMode) -> bool:
        """Update metadata without modifying history, timestamps or memory job state."""
        return await _joined_write(self._set_thread_memory_mode, thread_id, ThreadMemoryMode(mode))

    def _set_thread_memory_mode(self, thread_id: ThreadId, mode: ThreadMemoryMode) -> bool:
        with self._connect() as connection:
            return (
                connection.execute(
                    "UPDATE threads SET memory_mode=? WHERE id=?", (mode.value, str(thread_id))
                ).rowcount
                > 0
            )

    def _latest_thread(self, cwd: Path | None) -> ThreadId | None:
        query = "SELECT id FROM threads WHERE archived_at IS NULL"
        parameters: tuple[str, ...] = ()
        if cwd is not None:
            query += " AND cwd=?"
            parameters = (str(cwd.resolve()),)
        query += " ORDER BY updated_at DESC, created_at DESC, rowid DESC LIMIT 1"
        with self._connect() as connection:
            row = connection.execute(query, parameters).fetchone()
        return ThreadId(row["id"]) if row else None

    def resolve_thread(self, reference: str, cwd: Path) -> ThreadId:
        """Resolve ``latest`` or an exact persisted thread id synchronously."""

        if reference == "latest":
            thread_id = self._latest_thread(cwd)
            if thread_id is None:
                raise ValueError(f"no previous Corki thread for {cwd}")
            return thread_id
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, archived_at FROM threads WHERE id=?", (reference,)
            ).fetchone()
        if row is None:
            raise ValueError(f"unknown Corki thread: {reference}")
        if row["archived_at"] is not None:
            raise ValueError(f"thread {reference} is archived; unarchive it before resuming")
        return ThreadId(row["id"])

    async def save_turn(self, turn: TurnRecord) -> None:
        await _joined_write(self._save_turn, turn)

    async def confirm_turn_terminal(self, turn: TurnRecord) -> bool:
        if turn.status is TurnStatus.RUNNING:
            raise ValueError("cannot confirm a running Turn as terminal")
        return await asyncio.to_thread(self._confirm_turn_terminal, turn)

    async def retry_turn_terminal(self, turn: TurnRecord) -> None:
        """Retry a known terminal without overwriting another admission or result."""
        if turn.status is TurnStatus.RUNNING:
            raise ValueError("cannot retry a running Turn as terminal")
        await _joined_write(self._retry_turn_terminal, turn)

    def _retry_turn_terminal(self, turn: TurnRecord) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT thread_id, user_input, operation, status, final_answer, error "
                "FROM turns WHERE id=?",
                (str(turn.id),),
            ).fetchone()
            if row is None or tuple(row[:3]) != (
                str(turn.thread_id),
                turn.user_input,
                turn.operation,
            ):
                raise StorageIntegrityError("terminal retry does not match admitted Turn")
            if row[3] != TurnStatus.RUNNING.value:
                if tuple(row[3:]) == (turn.status.value, turn.final_answer, turn.error):
                    return
                raise StorageIntegrityError("terminal retry conflicts with stored result")
            connection.execute(
                "UPDATE turns SET status=?, final_answer=?, error=?, "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id=?",
                (turn.status.value, turn.final_answer, turn.error, str(turn.id)),
            )
            connection.execute(
                "UPDATE threads SET updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id=?",
                (str(turn.thread_id),),
            )

    def _confirm_turn_terminal(self, turn: TurnRecord) -> bool:
        # A fresh connection observes the committed transaction, not a cache or
        # the failed writer's state. Preserve admission-only model/base fields.
        with self._connect() as connection:
            row = connection.execute(
                """SELECT 1 FROM turns
                   WHERE id=? AND thread_id=? AND status=? AND user_input=?
                     AND operation=? AND final_answer IS ? AND error IS ?""",
                (
                    str(turn.id),
                    str(turn.thread_id),
                    turn.status.value,
                    turn.user_input,
                    turn.operation,
                    turn.final_answer,
                    turn.error,
                ),
            ).fetchone()
        return row is not None

    async def latest_running_turn(self, thread_id: ThreadId) -> TurnRecord | None:
        return await asyncio.to_thread(self._latest_running_turn, thread_id)

    async def load_turn_status(self, thread_id: ThreadId, turn_id: TurnId) -> TurnStatus | None:
        return await asyncio.to_thread(self._load_turn_status, thread_id, turn_id)

    def _load_turn_status(self, thread_id: ThreadId, turn_id: TurnId) -> TurnStatus | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM turns WHERE thread_id=? AND id=?",
                (str(thread_id), str(turn_id)),
            ).fetchone()
        if row is None:
            return None
        try:
            return TurnStatus(row["status"])
        except ValueError as error:
            raise StorageIntegrityError("invalid stored Turn status") from error

    def _latest_running_turn(self, thread_id: ThreadId) -> TurnRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, thread_id, status, user_input, final_answer, error, operation,
                       model_settings_json, base_instructions
                FROM turns WHERE thread_id=? AND status='running'
                ORDER BY updated_at DESC, rowid DESC LIMIT 1
                """,
                (str(thread_id),),
            ).fetchone()
        if row is None:
            return None
        from corki.sessions.models import TurnStatus

        try:
            model_settings = (
                ModelSettingsSnapshot.from_payload(json.loads(row["model_settings_json"]))
                if row["model_settings_json"] is not None
                else None
            )
        except (ValueError, TypeError) as error:
            raise StorageIntegrityError("invalid stored Turn model settings") from error

        return TurnRecord(
            TurnId(row["id"]),
            ThreadId(row["thread_id"]),
            TurnStatus(row["status"]),
            row["user_input"],
            row["final_answer"],
            row["error"],
            row["operation"],
            model_settings,
            row["base_instructions"],
        )

    def _save_turn(self, turn: TurnRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO turns(id, thread_id, status, user_input, final_answer, error, operation,
                                  model_settings_json, base_instructions)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    final_answer=excluded.final_answer,
                    error=excluded.error,
                    updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                """,
                (
                    str(turn.id),
                    str(turn.thread_id),
                    turn.status.value,
                    turn.user_input,
                    turn.final_answer,
                    turn.error,
                    turn.operation,
                    json.dumps(turn.model_settings.to_payload()) if turn.model_settings else None,
                    turn.base_instructions,
                ),
            )
            connection.execute(
                "UPDATE threads SET updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id=?",
                (str(turn.thread_id),),
            )

    async def append_items(self, thread_id: ThreadId, items: tuple[ConversationItem, ...]) -> None:
        await _joined_write(self._append_items, thread_id, items)

    def _append_items(self, thread_id: ThreadId, items: tuple[ConversationItem, ...]) -> None:
        if not items:
            return
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._append_items_in_connection(connection, thread_id, items)

    @staticmethod
    def _append_items_in_connection(
        connection: sqlite3.Connection,
        thread_id: ThreadId,
        items: tuple[ConversationItem, ...],
    ) -> None:
        sequence = connection.execute(
            "SELECT COALESCE(MAX(sequence), -1) + 1 FROM conversation_items WHERE thread_id=?",
            (str(thread_id),),
        ).fetchone()[0]
        for item in items:
            payload = item_to_payload(item)
            existing = connection.execute(
                "SELECT thread_id, turn_id, kind, payload_json FROM conversation_items WHERE id=?",
                (str(item.id),),
            ).fetchone()
            if existing is not None:
                existing_payload = json.loads(existing["payload_json"])
                if isinstance(item, AssistantMessageItem):
                    existing_payload.setdefault("phase", None)
                normalized_payload = json.loads(json.dumps(payload, ensure_ascii=False))
                # Recovery may reconstruct a deterministic item after restart,
                # so its wall-clock creation metadata can differ while its
                # durable semantic identity and contents remain identical.
                existing_payload.pop("created_at", None)
                normalized_payload.pop("created_at", None)
                same_item = (
                    existing["thread_id"] == str(thread_id)
                    and existing["turn_id"] == str(item.turn_id)
                    and existing["kind"] == item_kind(item)
                    and existing_payload == normalized_payload
                )
                if same_item:
                    continue
                raise StorageIntegrityError(
                    f"conversation item id collision with different data: {item.id}"
                )
            connection.execute(
                """
                INSERT INTO conversation_items(
                    id, thread_id, turn_id, sequence, kind, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(item.id),
                    str(thread_id),
                    str(item.turn_id),
                    sequence,
                    item_kind(item),
                    json.dumps(payload, ensure_ascii=False),
                    item.created_at,
                ),
            )
            sequence += 1
            if preview := user_preview(item):
                connection.execute(
                    "UPDATE threads SET preview=? WHERE id=? AND preview=''",
                    (preview, str(thread_id)),
                )

    async def load_items(self, thread_id: ThreadId) -> tuple[ConversationItem, ...]:
        return await asyncio.to_thread(self._load_items, thread_id)

    async def load_display_snapshot(self, thread_id: ThreadId) -> DisplayHistory:
        return await asyncio.to_thread(self._load_display_snapshot, thread_id)

    async def load_display_items_page(
        self, thread_id: ThreadId, *, cursor: DisplayItemsCursor | None = None, limit: int = 100
    ) -> DisplayItemsPage:
        return await asyncio.to_thread(
            read_display_items_page, self._connect, thread_id, cursor, limit
        )

    async def load_display_turns_page(
        self, thread_id: ThreadId, *, cursor: DisplayTurnsCursor | None = None, limit: int = 100
    ) -> DisplayTurnsPage:
        return await asyncio.to_thread(
            read_display_turns_page, self._connect, thread_id, cursor, limit
        )

    async def contains_display_item(self, thread_id, *, kind, identity, through_sequence):
        return await asyncio.to_thread(
            contains_display_item, self._connect, thread_id, kind, identity, through_sequence
        )

    def _load_display_snapshot(self, thread_id: ThreadId) -> DisplayHistory:
        with self._connect() as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                "SELECT kind, payload_json FROM conversation_items "
                "WHERE thread_id=? ORDER BY sequence",
                (str(thread_id),),
            ).fetchall()
            turns = connection.execute(
                "SELECT id,status,error,model_settings_json FROM turns "
                "WHERE thread_id=? ORDER BY rowid",
                (str(thread_id),),
            ).fetchall()
        return DisplayHistory(
            tuple(item_from_payload(row["kind"], json.loads(row["payload_json"])) for row in rows),
            tuple(display_turn_from_row(row) for row in turns),
        )

    def _load_items(self, thread_id: ThreadId) -> tuple[ConversationItem, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT kind, payload_json FROM conversation_items "
                "WHERE thread_id=? ORDER BY sequence",
                (str(thread_id),),
            ).fetchall()
        return tuple(
            item_from_payload(row["kind"], json.loads(row["payload_json"])) for row in rows
        )

    async def save_messages(self, thread_id: ThreadId, messages: tuple[Message, ...]) -> None:
        """Compatibility bridge for pre-item callers and existing installations."""

        await self.append_items(thread_id, items_from_messages(messages))

    async def load_messages(self, thread_id: ThreadId) -> tuple[Message, ...]:
        return _messages_from_items(await self.load_items(thread_id))

    async def load_hook_batch(self, thread_id, turn_id, batch_key):
        return await _joined_write(self._load_hook_batch, thread_id, turn_id, batch_key)

    async def load_hook_batch_keys(self, thread_id, prefix):
        return await _joined_write(self._load_hook_batch_keys, thread_id, prefix)

    def _load_hook_batch_keys(self, thread_id, prefix):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT turn_id, batch_key FROM hook_batches WHERE thread_id=? "
                "AND substr(batch_key,1,length(?))=? ORDER BY rowid",
                (str(thread_id), prefix, prefix),
            ).fetchall()
        return tuple((row["turn_id"], row["batch_key"]) for row in rows)

    def _load_hook_batch(self, thread_id, turn_id, batch_key):
        with self._connect() as connection:
            connection.execute("BEGIN")
            batch = connection.execute(
                "SELECT thread_id, turn_id, snapshot_json FROM hook_batches WHERE batch_key=?",
                (batch_key,),
            ).fetchone()
            rows = connection.execute(
                "SELECT execution_key, request_json, result_json FROM hook_executions "
                "WHERE thread_id=? AND turn_id=? AND substr(execution_key,1,length(?))=?",
                (str(thread_id), str(turn_id), batch_key, batch_key),
            ).fetchall()
        if batch is None:
            if rows:
                raise StorageIntegrityError(
                    "Legacy hook execution has no batch snapshot; safe recovery cannot be proven."
                )
            return None
        if (batch["thread_id"], batch["turn_id"]) != (str(thread_id), str(turn_id)):
            raise StorageIntegrityError("hook batch identity collision")
        return json.loads(batch["snapshot_json"]), {
            row["execution_key"]: {
                "request": json.loads(row["request_json"]),
                "result": json.loads(row["result_json"])
                if row["result_json"] is not None
                else None,
            }
            for row in rows
        }

    async def save_hook_batch(self, thread_id, turn_id, batch_key, snapshot):
        encoded = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, allow_nan=False)
        await _joined_write(self._save_hook_batch, thread_id, turn_id, batch_key, encoded)

    def _save_hook_batch(self, thread_id, turn_id, batch_key, encoded):
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT thread_id, turn_id, snapshot_json FROM hook_batches WHERE batch_key=?",
                (batch_key,),
            ).fetchone()
            identity = (str(thread_id), str(turn_id), encoded)
            if row is not None:
                if tuple(row) != identity:
                    raise StorageIntegrityError("hook batch identity collision")
                return
            connection.execute(
                "INSERT INTO hook_batches(batch_key, thread_id, turn_id, snapshot_json) "
                "VALUES (?, ?, ?, ?)",
                (batch_key, *identity),
            )

    async def load_hook_executions(self, thread_id, turn_id, prefix):
        return await _joined_write(self._load_hook_executions, thread_id, turn_id, prefix)

    def _load_hook_executions(self, thread_id, turn_id, prefix):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT execution_key, request_json, result_json FROM hook_executions "
                "WHERE thread_id=? AND turn_id=? AND substr(execution_key,1,length(?))=? "
                "ORDER BY rowid",
                (str(thread_id), str(turn_id), prefix, prefix),
            ).fetchall()
        return tuple(
            (row[0], json.loads(row[1]), json.loads(row[2]) if row[2] is not None else None)
            for row in rows
        )

    async def has_hook_executions(self, thread_id, turn_id, prefix) -> bool:
        # Join the connection-owning worker even when its caller is cancelled.
        return await _joined_write(self._has_hook_executions, thread_id, turn_id, prefix)

    def _has_hook_executions(self, thread_id, turn_id, prefix):
        with self._connect() as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM hook_executions WHERE thread_id=? AND turn_id=? "
                    "AND substr(execution_key, 1, length(?))=? LIMIT 1",
                    (str(thread_id), str(turn_id), prefix, prefix),
                ).fetchone()
                is not None
            )

    async def claim_hook_execution(self, thread_id, turn_id, execution_key, request) -> dict | None:
        """Claim a non-tool side effect; an unfinished claim is never replayable."""
        encoded = json.dumps(request, sort_keys=True, ensure_ascii=False, allow_nan=False)
        return await _joined_write(
            self._claim_hook_execution, thread_id, turn_id, execution_key, encoded
        )

    def _claim_hook_execution(self, thread_id, turn_id, execution_key, encoded):
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT thread_id, turn_id, request_json, result_json FROM hook_executions "
                "WHERE execution_key=?",
                (execution_key,),
            ).fetchone()
            if row is not None:
                if (row["thread_id"], row["turn_id"], row["request_json"]) != (
                    str(thread_id),
                    str(turn_id),
                    encoded,
                ):
                    raise StorageIntegrityError("hook execution identity collision")
                if row["result_json"] is None:
                    raise RuntimeError(
                        "Previous hook outcome is unknown; execution was not repeated."
                    )
                return json.loads(row["result_json"])
            connection.execute(
                "INSERT INTO hook_executions(execution_key, thread_id, turn_id, request_json) "
                "VALUES (?, ?, ?, ?)",
                (execution_key, str(thread_id), str(turn_id), encoded),
            )
        return None

    async def complete_hook_execution(
        self, thread_id, turn_id, execution_key, request, result
    ) -> None:
        if not isinstance(result, dict):
            raise ValueError("hook execution result must be an object")
        request_json = json.dumps(request, sort_keys=True, ensure_ascii=False, allow_nan=False)
        result_json = json.dumps(result, sort_keys=True, ensure_ascii=False, allow_nan=False)
        await _joined_write(
            self._complete_hook_execution,
            thread_id,
            turn_id,
            execution_key,
            request_json,
            result_json,
        )

    def _complete_hook_execution(
        self, thread_id, turn_id, execution_key, request_json, result_json
    ):
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT thread_id, turn_id, request_json, result_json FROM hook_executions "
                "WHERE execution_key=?",
                (execution_key,),
            ).fetchone()
            if row is None or (row["thread_id"], row["turn_id"], row["request_json"]) != (
                str(thread_id),
                str(turn_id),
                request_json,
            ):
                raise StorageIntegrityError("hook execution claim is missing or belongs elsewhere")
            if row["result_json"] is not None and row["result_json"] != result_json:
                raise StorageIntegrityError("completed hook result cannot be overwritten")
            connection.execute(
                "UPDATE hook_executions SET result_json=? WHERE execution_key=?",
                (result_json, execution_key),
            )

    async def claim_tool_call(
        self, thread_id: ThreadId, turn_id: TurnId, call: ToolCall
    ) -> ToolResult | None:
        # A claim can insert a ledger row. Own that write through cancellation
        # before cleanup or storage shutdown is allowed to proceed.
        return await _joined_write(self._claim_tool_call, thread_id, turn_id, call)

    def _claim_tool_call(
        self, thread_id: ThreadId, turn_id: TurnId, call: ToolCall
    ) -> ToolResult | None:
        fingerprint = _call_arguments_fingerprint(call)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status, result_json, thread_id, turn_id, tool_name, arguments_sha256
                FROM tool_executions WHERE call_id=?
                """,
                (str(call.id),),
            ).fetchone()
            if row is not None:
                if (
                    row["thread_id"] != str(thread_id)
                    or row["turn_id"] != str(turn_id)
                    or row["tool_name"] != call.name
                    or (
                        row["arguments_sha256"] is not None
                        and row["arguments_sha256"] != fingerprint
                    )
                ):
                    return ToolResult(
                        call.id,
                        call.name,
                        "Tool call id collision; execution was refused.",
                        is_error=True,
                        dispatch_error=True,
                    )
                if row["arguments_sha256"] is None:
                    # History alone cannot establish which payload produced
                    # an old result: a later colliding call may already have
                    # been appended. Do not backfill from unproven evidence.
                    return ToolResult(
                        call.id,
                        call.name,
                        "Previous tool arguments are unverified (legacy ledger); "
                        "the cached result was not reused and execution was not repeated.",
                        is_error=True,
                        dispatch_error=True,
                    )
                if row["status"] == "completed" and row["result_json"]:
                    return _result_from_json(row["result_json"])
                return ToolResult(
                    call.id,
                    call.name,
                    "Previous execution was interrupted; outcome is unknown and was not repeated.",
                    is_error=True,
                    dispatch_error=True,
                )
            connection.execute(
                """
                INSERT INTO tool_executions(
                    call_id, thread_id, turn_id, tool_name, arguments_sha256, status
                )
                VALUES (?, ?, ?, ?, ?, 'running')
                """,
                (str(call.id), str(thread_id), str(turn_id), call.name, fingerprint),
            )
        return None

    async def load_turn_tool_outcomes(self, thread_id, turn_id) -> tuple[ToolResult, ...]:
        def read():
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT call_id, tool_name, status, result_json FROM tool_executions "
                    "WHERE thread_id=? AND turn_id=? ORDER BY rowid",
                    (str(thread_id), str(turn_id)),
                ).fetchall()
            return tuple(
                _result_from_json(row["result_json"])
                if row["status"] == "completed" and row["result_json"]
                else ToolResult(
                    ToolCallId(row["call_id"]),
                    row["tool_name"],
                    "Previous execution outcome is unknown; not repeated.",
                    is_error=True,
                    dispatch_error=True,
                )
                for row in rows
            )

        return await asyncio.to_thread(read)

    async def code_mode_parent_finished(self, thread_id, call_id):
        """A committed running exec observation is not a finished script."""

        def read():
            with self._connect() as connection:
                connection.execute("BEGIN")
                row = connection.execute(
                    "SELECT status, result_json FROM tool_executions "
                    "WHERE thread_id=? AND call_id=?",
                    (str(thread_id), str(call_id)),
                ).fetchone()
                observations = connection.execute(
                    "SELECT result_json FROM tool_executions "
                    "WHERE thread_id=? AND status='completed' AND result_json IS NOT NULL",
                    (str(thread_id),),
                ).fetchall()
            if row is None:
                raise StorageIntegrityError("PostToolUse parent execution is missing")
            if row["status"] != "completed" or row["result_json"] is None:
                return False
            parent = _result_from_json(row["result_json"])
            if parent.code_mode_lifecycle_json is None:
                return True  # Legacy results have no authoritative cell status.
            identity = json.loads(parent.code_mode_lifecycle_json)
            if identity["parent_call_id"] != str(call_id):
                raise StorageIntegrityError("Code Mode parent identity collision")
            if identity["status"] != "running":
                return True
            for observation in observations:
                result = _result_from_json(observation["result_json"])
                if result.code_mode_lifecycle_json is None:
                    continue
                value = json.loads(result.code_mode_lifecycle_json)
                if (value["cell_id"], value["parent_call_id"]) == (
                    identity["cell_id"],
                    str(call_id),
                ) and value["status"] != "running":
                    return True
            return False

        return await asyncio.to_thread(read)

    async def complete_tool_call(
        self, thread_id: ThreadId, turn_id: TurnId, result: ToolResult, post_hook_batch=None
    ) -> None:
        # Cancellation cleanup consults this ledger to reconstruct observations.
        # Do not let it see "running" while an abandoned thread commits success.
        await _joined_write(self._complete_tool_call, thread_id, turn_id, result, post_hook_batch)

    def _complete_tool_call(
        self, thread_id: ThreadId, turn_id: TurnId, result: ToolResult, post_hook_batch=None
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, result_json, tool_name FROM tool_executions "
                "WHERE call_id=? AND thread_id=? AND turn_id=?",
                (str(result.call_id), str(thread_id), str(turn_id)),
            ).fetchone()
            if row is None or row["tool_name"] != result.tool_name:
                raise StorageIntegrityError(
                    "tool execution entry is missing or belongs to a different tool"
                )
            encoded = _result_to_json(result)
            if post_hook_batch is not None:
                key, snapshot = post_hook_batch
                expected = f"post_tool_use:{thread_id}:{turn_id}:{result.call_id}:"
                if key != expected:
                    raise StorageIntegrityError("PostToolUse batch does not belong to tool result")
                snapshot_json = json.dumps(
                    snapshot, sort_keys=True, ensure_ascii=False, allow_nan=False
                )
                saved = connection.execute(
                    "SELECT thread_id, turn_id, snapshot_json FROM hook_batches WHERE batch_key=?",
                    (key,),
                ).fetchone()
                identity = (str(thread_id), str(turn_id), snapshot_json)
                if saved is not None and tuple(saved) != identity:
                    raise StorageIntegrityError("hook batch identity collision")
                if saved is None:
                    if row["status"] == "completed":
                        raise StorageIntegrityError(
                            "completed tool cannot acquire a new Hook batch"
                        )
                    connection.execute(
                        "INSERT INTO hook_batches(batch_key, thread_id, turn_id, snapshot_json) "
                        "VALUES (?, ?, ?, ?)",
                        (key, *identity),
                    )
            if row["status"] == "completed":
                if row["result_json"] is not None and json.loads(row["result_json"]) == json.loads(
                    encoded
                ):
                    return
                raise StorageIntegrityError(
                    "completed tool result cannot be overwritten with different data"
                )
            cursor = connection.execute(
                """
                UPDATE tool_executions
                SET status='completed', result_json=?, completed_at=CURRENT_TIMESTAMP
                WHERE call_id=? AND thread_id=? AND turn_id=?
                """,
                (
                    encoded,
                    str(result.call_id),
                    str(thread_id),
                    str(turn_id),
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("tool execution ledger entry is missing or belongs elsewhere")

    async def load_partial_step(
        self, thread_id: ThreadId, turn_id: TurnId, step_index: int
    ) -> tuple[ConversationItem, ...]:
        return await asyncio.to_thread(self._load_partial_step, thread_id, turn_id, step_index)

    def _load_partial_step(
        self, thread_id: ThreadId, turn_id: TurnId, step_index: int
    ) -> tuple[ConversationItem, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT c.kind, c.payload_json FROM partial_model_items p
                   JOIN conversation_items c ON c.id=p.item_id
                   WHERE p.thread_id=? AND p.turn_id=? AND p.step_index=? ORDER BY p.ordinal""",
                (str(thread_id), str(turn_id), step_index),
            ).fetchall()
        return tuple(
            item_from_payload(row["kind"], json.loads(row["payload_json"])) for row in rows
        )

    async def append_partial_item(
        self, thread_id: ThreadId, turn_id: TurnId, step_index: int, item: ConversationItem
    ) -> None:
        # Cancellation must join the write: the caller can then reconcile the
        # exact set of durable calls before closing the turn.
        await _joined_write(self._append_partial_item, thread_id, turn_id, step_index, item)

    def _append_partial_item(
        self, thread_id: ThreadId, turn_id: TurnId, step_index: int, item: ConversationItem
    ) -> None:
        if item.turn_id != turn_id:
            raise StorageIntegrityError("partial item belongs to another turn")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._append_items_in_connection(connection, thread_id, (item,))
            ordinal = connection.execute(
                """SELECT COALESCE(MAX(ordinal), -1)+1 FROM partial_model_items
                   WHERE thread_id=? AND turn_id=? AND step_index=?""",
                (str(thread_id), str(turn_id), step_index),
            ).fetchone()[0]
            connection.execute(
                """INSERT OR IGNORE INTO partial_model_items
                   (thread_id, turn_id, step_index, item_id, ordinal) VALUES (?, ?, ?, ?, ?)""",
                (str(thread_id), str(turn_id), step_index, str(item.id), ordinal),
            )

    async def load_model_step(
        self, thread_id: ThreadId, turn_id: TurnId, step_index: int
    ) -> ModelCompleted | None:
        return await asyncio.to_thread(self._load_model_step, thread_id, turn_id, step_index)

    async def load_context_usage(self, thread_id: ThreadId) -> ContextUsage | None:
        return await asyncio.to_thread(self._load_context_usage, thread_id)

    def _load_context_usage(self, thread_id: ThreadId) -> ContextUsage | None:
        from corki.storage.usage import context_usage, model_usage_fact

        with self._connect() as connection:
            connection.execute("BEGIN")
            row = connection.execute(
                "SELECT * FROM model_steps WHERE thread_id=? ORDER BY rowid DESC LIMIT 1",
                (str(thread_id),),
            ).fetchone()
            if row is not None:
                return context_usage(model_usage_fact(row))
            inherited = connection.execute(
                "SELECT total_tokens,anchor_id,input_tokens,sample_id "
                "FROM inherited_context_usage WHERE thread_id=? ORDER BY ordinal DESC LIMIT 1",
                (str(thread_id),),
            ).fetchone()
            return context_usage(inherited)

    def _load_model_step(
        self, thread_id: ThreadId, turn_id: TurnId, step_index: int
    ) -> ModelCompleted | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT input_tokens, output_tokens, cached_tokens, reasoning_tokens,
                       metadata_json, items_json, end_turn, total_tokens, usage_details_json
                FROM model_steps
                WHERE thread_id=? AND turn_id=? AND step_index=?
                """,
                (str(thread_id), str(turn_id), step_index),
            ).fetchone()
        if row is None:
            return None
        return _completed_from_row(row)

    async def commit_model_step(
        self,
        thread_id: ThreadId,
        turn_id: TurnId,
        step_index: int,
        completed: ModelCompleted,
    ) -> None:
        await _joined_write(
            self._commit_model_step,
            thread_id,
            turn_id,
            step_index,
            completed,
        )

    def _commit_model_step(
        self,
        thread_id: ThreadId,
        turn_id: TurnId,
        step_index: int,
        completed: ModelCompleted,
    ) -> None:
        step_id = next(
            (str(item.step_id) for item in completed.items if hasattr(item, "step_id")),
            f"empty-{turn_id}-{step_index}",
        )
        serialized_items = [
            {"kind": item_kind(item), "payload": item_to_payload(item)} for item in completed.items
        ]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if (
                connection.execute(
                    "SELECT 1 FROM model_failures WHERE thread_id=? AND turn_id=? AND step_index=?",
                    (str(thread_id), str(turn_id), step_index),
                ).fetchone()
                is not None
            ):
                raise StorageIntegrityError("cannot complete a failed model attempt")
            existing = connection.execute(
                """
                SELECT input_tokens, output_tokens, cached_tokens, reasoning_tokens,
                       metadata_json, items_json, end_turn, total_tokens, usage_details_json
                FROM model_steps
                WHERE thread_id=? AND turn_id=? AND step_index=?
                """,
                (str(thread_id), str(turn_id), step_index),
            ).fetchone()
            if existing is not None:
                if _completed_from_row(existing) == completed:
                    return
                raise RuntimeError(f"conflicting model result for turn {turn_id} step {step_index}")
            connection.execute(
                """
                INSERT INTO model_steps(
                    step_id, thread_id, turn_id, input_tokens, output_tokens,
                    cached_tokens, reasoning_tokens, metadata_json, step_index, items_json,
                    end_turn, usage_details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    step_id,
                    str(thread_id),
                    str(turn_id),
                    completed.usage.input_tokens,
                    completed.usage.output_tokens,
                    completed.usage.cached_tokens,
                    completed.usage.reasoning_tokens,
                    dumps_wire(dict(completed.provider_metadata)),
                    step_index,
                    json.dumps(serialized_items, ensure_ascii=False),
                    completed.end_turn,
                    dumps_wire(
                        {
                            "cache_write_tokens": completed.usage.cache_write_tokens,
                            "codex_rollout_budget_units": (
                                completed.usage.codex_rollout_budget_units
                            ),
                        }
                    ),
                ),
            )
            self._append_items_in_connection(connection, thread_id, completed.items)
            if completed.items:
                anchor = str(completed.items[-1].id)
            else:
                last = connection.execute(
                    "SELECT id FROM conversation_items WHERE thread_id=? "
                    "ORDER BY sequence DESC LIMIT 1",
                    (str(thread_id),),
                ).fetchone()
                anchor = last["id"] if last is not None else None
            connection.execute(
                "UPDATE model_steps SET total_tokens=?, usage_anchor_id=? "
                "WHERE thread_id=? AND turn_id=? AND step_index=?",
                (completed.usage.total_tokens, anchor, str(thread_id), str(turn_id), step_index),
            )

    async def close(self) -> None:
        return None

    async def load_model_failure(self, thread_id, turn_id, step_index) -> ModelFailure | None:
        return await asyncio.to_thread(self._load_model_failure, thread_id, turn_id, step_index)

    def _load_model_failure(self, thread_id, turn_id, step_index):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM model_failures "
                "WHERE thread_id=? AND turn_id=? AND step_index=?",
                (str(thread_id), str(turn_id), step_index),
            ).fetchone()
        return ModelFailure(**json.loads(row[0])) if row is not None else None

    async def save_model_failure(
        self, thread_id, turn_id, step_index, failure: ModelFailure
    ) -> None:
        await _joined_write(self._save_model_failure, thread_id, turn_id, step_index, failure)

    def _save_model_failure(self, thread_id, turn_id, step_index, failure):
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if (
                connection.execute(
                    "SELECT 1 FROM model_steps WHERE thread_id=? AND turn_id=? AND step_index=?",
                    (str(thread_id), str(turn_id), step_index),
                ).fetchone()
                is not None
            ):
                raise StorageIntegrityError("cannot fail a completed model attempt")
            payload = asdict(failure)
            existing = connection.execute(
                "SELECT payload_json FROM model_failures "
                "WHERE thread_id=? AND turn_id=? AND step_index=?",
                (str(thread_id), str(turn_id), step_index),
            ).fetchone()
            if existing is not None:
                if ModelFailure(**json.loads(existing[0])) != failure:
                    raise StorageIntegrityError("conflicting model failure for the same attempt")
                return
            connection.execute(
                "INSERT INTO model_failures VALUES (?, ?, ?, ?)",
                (str(thread_id), str(turn_id), step_index, json.dumps(payload)),
            )


async def _joined_write(write, *args):
    def commit_and_publish():
        result = write(*args)
        owner = getattr(write, "__self__", None)
        publish = getattr(owner, "_after_durable_write", None)
        if publish is not None:
            try:
                publish()
            except Exception:
                logging.getLogger(__name__).warning(
                    "Post-commit transcript refresh failed", exc_info=True
                )
        return result

    task = asyncio.create_task(asyncio.to_thread(commit_and_publish))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled() and task.exception() is not None:
            logging.getLogger(__name__).warning(
                "Durable write failed during cancellation", exc_info=task.exception()
            )
        raise


def _call_arguments_fingerprint(call: ToolCall) -> str:
    # Bind both the decoded cache and raw-authoritative handlers. Keep old hashes
    # when these agree; rounded caches cannot prove an old exact remote payload.
    value = (
        {"parsed": dict(call.arguments)}
        if call.arguments is not None and call.parse_error is None
        else {"raw": call.raw_arguments, "parsed": call.arguments}
    )
    if call.input_kind == "freeform":
        value = {"freeform": call.raw_arguments, "parse_error": call.parse_error}
    elif call.raw_arguments and call.arguments is not None and call.parse_error is None:
        try:
            raw_value = materialize(loads_wire(call.raw_arguments), preserve_pairs=False)
            canonical = dumps_wire(raw_value, ensure_ascii=True, sort_keys=True)
        except (ValueError, RecursionError):
            value["raw_input"] = call.raw_arguments
        else:
            if canonical != dumps_wire(dict(call.arguments), ensure_ascii=True, sort_keys=True):
                value["raw_value_json"] = canonical
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return sha256(encoded.encode("ascii")).hexdigest()


def _result_to_json(result: ToolResult) -> str:
    from corki.protocol.tools import tool_spec_to_payload

    value = {
        "call_id": str(result.call_id),
        "tool_name": result.tool_name,
        "discovered_tools": [tool_spec_to_payload(spec) for spec in result.discovered_tools],
        "content": result.content,
        "is_error": result.is_error,
        "display_content": result.display_content,
        "attachments": [
            {"data_url": attachment.data_url, "detail": attachment.detail}
            for attachment in result.attachments
        ],
        "plan": [dict(item) for item in result.state_update.plan]
        if result.state_update.plan is not None
        else None,
    }
    # Omit the absent override: old completed ledger rows are immutable and
    # must remain byte-compatible. A present wrapper may explicitly hold null.
    if result.state_update.plan_explanation is not None:
        value["plan_explanation"] = result.state_update.plan_explanation
    if result.code_mode_output is not None:
        value["code_mode_output"] = {"value": result.code_mode_output.value}
    if result.content_items:
        value["content_items"] = [content_to_payload(part) for part in result.content_items]
    if result.dispatch_error:
        value["dispatch_error"] = True
    if result.contains_external_context:
        value["contains_external_context"] = True
    for key in (
        "fallback_token_limit_override",
        "legacy_output_char_budget",
        "is_tool_search_output",
        "mcp_result_json",
        "mcp_error",
        "patch_delta_json",
        "execution_input_json",
        "post_tool_use_json",
        "code_mode_lifecycle_json",
    ):
        if getattr(result, key) is not None:
            value[key] = getattr(result, key)
    if result.state_update.new_context_requested:
        value["new_context_requested"] = True
    return dumps_wire(value, separators=(", ", ": "))


def _completed_from_row(row: sqlite3.Row) -> ModelCompleted:
    serialized_items = json.loads(row["items_json"])
    items = tuple(item_from_payload(value["kind"], value["payload"]) for value in serialized_items)
    details = materialize(loads_wire(row["usage_details_json"]), preserve_pairs=False)
    return ModelCompleted(
        items,
        usage=ModelUsage(
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cached_tokens=row["cached_tokens"],
            reasoning_tokens=row["reasoning_tokens"],
            total_tokens=row["total_tokens"],
            cache_write_tokens=details.get("cache_write_tokens", 0),
            codex_rollout_budget_units=details.get("codex_rollout_budget_units"),
        ),
        provider_metadata=materialize(loads_wire(row["metadata_json"]), preserve_pairs=False),
        end_turn=None if row["end_turn"] is None else bool(row["end_turn"]),
    )


def _result_from_json(value: str) -> ToolResult:
    raw = loads_number_values(value)
    plan = raw.get("plan")
    return ToolResult(
        call_id=ToolCallId(raw["call_id"]),
        tool_name=raw["tool_name"],
        content=raw["content"],
        discovered_tools=tuple(
            tool_spec_from_payload(spec) for spec in raw.get("discovered_tools", ())
        ),
        is_error=bool(raw["is_error"]),
        dispatch_error=bool(raw.get("dispatch_error", False)),
        contains_external_context=raw.get("contains_external_context", False),
        fallback_token_limit_override=raw.get("fallback_token_limit_override"),
        legacy_output_char_budget=raw.get("legacy_output_char_budget"),
        is_tool_search_output=raw.get("is_tool_search_output"),
        mcp_result_json=raw.get("mcp_result_json"),
        mcp_error=raw.get("mcp_error"),
        patch_delta_json=raw.get("patch_delta_json"),
        execution_input_json=raw.get("execution_input_json"),
        post_tool_use_json=raw.get("post_tool_use_json"),
        code_mode_lifecycle_json=raw.get("code_mode_lifecycle_json"),
        display_content=raw.get("display_content"),
        content_items=tuple(content_from_payload(part) for part in raw.get("content_items", ())),
        code_mode_output=CodeModeOutput(raw["code_mode_output"]["value"])
        if "code_mode_output" in raw
        else None,
        attachments=tuple(ImageAttachment(**item) for item in raw.get("attachments", [])),
        state_update=ToolStateUpdate(
            plan=tuple(dict(item) for item in plan) if plan is not None else None,
            new_context_requested=raw.get("new_context_requested", False),
            plan_explanation=raw.get("plan_explanation"),
        ),
    )


def _legacy_row_to_message(row: sqlite3.Row) -> Message:
    calls = json.loads(row["tool_calls_json"])
    attachments = json.loads(row["attachments_json"])
    return Message(
        id=MessageId(row["id"]),
        turn_id=TurnId(row["turn_id"]),
        role=MessageRole(row["role"]),
        content=row["content"],
        tool_calls=tuple(
            ToolCall(
                id=ToolCallId(call["id"]),
                name=call["name"],
                arguments=call["arguments"],
                raw_arguments=call["raw_arguments"],
                parse_error=call["parse_error"],
            )
            for call in calls
        ),
        tool_call_id=ToolCallId(row["tool_call_id"]) if row["tool_call_id"] else None,
        name=row["name"],
        attachments=tuple(ImageAttachment(**attachment) for attachment in attachments),
        reasoning_content=row["reasoning_content"],
        created_at=row["created_at"],
    )


def _messages_from_items(items: tuple[ConversationItem, ...]) -> tuple[Message, ...]:
    messages: list[Message] = []
    assistant_groups: dict[ModelStepId, dict[str, Any]] = {}
    assistant_order: list[ModelStepId] = []
    for item in items:
        if isinstance(item, CompactionItem) and item.context_reset:
            continue
        if isinstance(item, ContextItem) and item.is_snapshot_only:
            continue
        if isinstance(item, (AssistantMessageItem, ReasoningItem, ToolCallItem)):
            group = assistant_groups.get(item.step_id)
            if group is None:
                group = {"content": "", "reasoning": "", "calls": [], "item": item}
                assistant_groups[item.step_id] = group
                assistant_order.append(item.step_id)
            if isinstance(item, AssistantMessageItem):
                group["content"] += item.content
            elif isinstance(item, ReasoningItem):
                group["reasoning"] += item.content
            else:
                group["calls"].append(item.call)
            continue
        _flush_legacy_assistants(messages, assistant_groups, assistant_order)
        if isinstance(item, HostedToolItem):
            messages.append(
                Message(
                    MessageRole.USER,
                    item.compatibility_content,
                    MessageId(str(item.id)),
                    item.turn_id,
                    created_at=item.created_at,
                )
            )
        elif isinstance(item, (UserMessageItem, TurnAbortedItem)):
            messages.append(
                Message(
                    MessageRole.USER,
                    item.content,
                    MessageId(str(item.id)),
                    item.turn_id,
                    attachments=item.attachments if isinstance(item, UserMessageItem) else (),
                    created_at=item.created_at,
                )
            )
        elif isinstance(item, ToolResultItem):
            messages.append(
                Message(
                    MessageRole.TOOL,
                    item.content,
                    MessageId(str(item.id)),
                    item.turn_id,
                    tool_call_id=item.call_id,
                    name=item.tool_name,
                    attachments=item.attachments,
                    created_at=item.created_at,
                )
            )
        elif isinstance(item, ContextItem):
            role = MessageRole.DEVELOPER if item.role is ContextRole.DEVELOPER else MessageRole.USER
            messages.append(
                Message(
                    role,
                    item.content,
                    MessageId(str(item.id)),
                    item.turn_id,
                    created_at=item.created_at,
                )
            )
        elif isinstance(item, BudgetNoticeItem):
            messages.append(
                Message(
                    MessageRole.DEVELOPER,
                    item.content,
                    MessageId(str(item.id)),
                    item.turn_id,
                    created_at=item.created_at,
                )
            )
        elif isinstance(item, CompactionItem):
            messages.append(
                Message(
                    MessageRole.DEVELOPER if item.remote_payload_json else MessageRole.USER,
                    item.summary
                    if item.remote_payload_json
                    else render_compaction_summary(item.summary),
                    MessageId(str(item.id)),
                    item.turn_id,
                    created_at=item.created_at,
                )
            )
    _flush_legacy_assistants(messages, assistant_groups, assistant_order)
    return tuple(messages)


def _flush_legacy_assistants(
    messages: list[Message],
    groups: dict[ModelStepId, dict[str, Any]],
    order: list[ModelStepId],
) -> None:
    for step_id in order:
        group = groups[step_id]
        item = group["item"]
        messages.append(
            Message(
                MessageRole.ASSISTANT,
                group["content"],
                MessageId(str(item.id)),
                item.turn_id,
                tool_calls=tuple(group["calls"]),
                reasoning_content=group["reasoning"] or None,
                created_at=item.created_at,
            )
        )
    groups.clear()
    order.clear()
