"""Versioned SQLite repository for threads, turns, items, and tool execution."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import Any

from corki.models.failure import ModelFailure
from corki.models.types import ModelCompleted, ModelUsage
from corki.protocol.ids import ItemId, MessageId, ModelStepId, ThreadId, ToolCallId, TurnId
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
from corki.protocol.messages import Message, MessageRole
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
from corki.sessions.models import ContextUsage, TurnRecord


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

    def _connect(self) -> sqlite3.Connection:
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
            turn_columns = {row[1] for row in connection.execute("PRAGMA table_info(turns)")}
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
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS model_steps_turn_index
                ON model_steps(thread_id, turn_id, step_index)
                WHERE step_index IS NOT NULL
                """
            )
            self._migrate_legacy_messages(connection)
            connection.execute(
                "UPDATE tool_executions SET status='interrupted' WHERE status='running'"
            )

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

    async def create_thread(self, thread_id: ThreadId, cwd: Path) -> None:
        await _joined_write(self._create_thread, thread_id, cwd)

    def _create_thread(self, thread_id: ThreadId, cwd: Path) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO threads(id, cwd) VALUES (?, ?)",
                (str(thread_id), str(cwd.resolve())),
            )

    async def latest_thread(self, cwd: Path | None = None) -> ThreadId | None:
        return await asyncio.to_thread(self._latest_thread, cwd)

    def _latest_thread(self, cwd: Path | None) -> ThreadId | None:
        query = "SELECT id FROM threads"
        parameters: tuple[str, ...] = ()
        if cwd is not None:
            query += " WHERE cwd=?"
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
            row = connection.execute("SELECT id FROM threads WHERE id=?", (reference,)).fetchone()
        if row is None:
            raise ValueError(f"unknown Corki thread: {reference}")
        return ThreadId(row["id"])

    async def save_turn(self, turn: TurnRecord) -> None:
        await _joined_write(self._save_turn, turn)

    async def latest_running_turn(self, thread_id: ThreadId) -> TurnRecord | None:
        return await asyncio.to_thread(self._latest_running_turn, thread_id)

    def _latest_running_turn(self, thread_id: ThreadId) -> TurnRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, thread_id, status, user_input, final_answer, error, operation
                FROM turns WHERE thread_id=? AND status='running'
                ORDER BY updated_at DESC, rowid DESC LIMIT 1
                """,
                (str(thread_id),),
            ).fetchone()
        if row is None:
            return None
        from corki.sessions.models import TurnStatus

        return TurnRecord(
            TurnId(row["id"]),
            ThreadId(row["thread_id"]),
            TurnStatus(row["status"]),
            row["user_input"],
            row["final_answer"],
            row["error"],
            row["operation"],
        )

    def _save_turn(self, turn: TurnRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO turns(id, thread_id, status, user_input, final_answer, error, operation)
                VALUES (?, ?, ?, ?, ?, ?, ?)
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
                ),
            )
            connection.execute(
                "UPDATE threads SET updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id=?",
                (str(turn.thread_id),),
            )

    async def append_items(self, thread_id: ThreadId, items: tuple[ConversationItem, ...]) -> None:
        await asyncio.to_thread(self._append_items, thread_id, items)

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

    async def load_items(self, thread_id: ThreadId) -> tuple[ConversationItem, ...]:
        return await asyncio.to_thread(self._load_items, thread_id)

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

    async def claim_tool_call(
        self, thread_id: ThreadId, turn_id: TurnId, call: ToolCall
    ) -> ToolResult | None:
        return await asyncio.to_thread(self._claim_tool_call, thread_id, turn_id, call)

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

    async def complete_tool_call(
        self, thread_id: ThreadId, turn_id: TurnId, result: ToolResult
    ) -> None:
        await asyncio.to_thread(self._complete_tool_call, thread_id, turn_id, result)

    def _complete_tool_call(self, thread_id: ThreadId, turn_id: TurnId, result: ToolResult) -> None:
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
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM model_steps WHERE thread_id=? ORDER BY rowid DESC LIMIT 1",
                (str(thread_id),),
            ).fetchone()
        if row is None:
            return None
        completed = _completed_from_row(row)
        total = completed.usage.context_tokens
        anchor = row["usage_anchor_id"]
        # Old rows with outputs retain an exact identity; empty legacy results
        # cannot establish a boundary and must use local estimation instead.
        if anchor is None and completed.items:
            anchor = str(completed.items[-1].id)
        if total is None or anchor is None:
            return None
        return ContextUsage(
            total,
            ItemId(anchor),
            completed.provider_metadata.get("server_reasoning_included") is True,
            input_tokens=completed.usage.input_tokens,
            sample_id=row["step_id"],
        )

    def _load_model_step(
        self, thread_id: ThreadId, turn_id: TurnId, step_index: int
    ) -> ModelCompleted | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT input_tokens, output_tokens, cached_tokens, reasoning_tokens,
                       metadata_json, items_json, end_turn, total_tokens
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
        await asyncio.to_thread(
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
                       metadata_json, items_json, end_turn, total_tokens
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
                    cached_tokens, reasoning_tokens, metadata_json, step_index, items_json, end_turn
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    step_id,
                    str(thread_id),
                    str(turn_id),
                    completed.usage.input_tokens,
                    completed.usage.output_tokens,
                    completed.usage.cached_tokens,
                    completed.usage.reasoning_tokens,
                    json.dumps(dict(completed.provider_metadata), ensure_ascii=False),
                    step_index,
                    json.dumps(serialized_items, ensure_ascii=False),
                    completed.end_turn,
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
    task = asyncio.create_task(asyncio.to_thread(write, *args))
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
    # Hash executable parsed arguments rather than JSON whitespace/key order.
    # Invalid arguments cannot execute, but distinct malformed calls still
    # must not accidentally reuse another call's recorded observation.
    value = (
        {"parsed": dict(call.arguments)}
        if call.arguments is not None and call.parse_error is None
        else {"raw": call.raw_arguments, "parsed": call.arguments}
    )
    if call.input_kind == "freeform":
        value = {"freeform": call.raw_arguments, "parse_error": call.parse_error}
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
    ):
        if getattr(result, key) is not None:
            value[key] = getattr(result, key)
    if result.state_update.new_context_requested:
        value["new_context_requested"] = True
    return json.dumps(value, ensure_ascii=False)


def _completed_from_row(row: sqlite3.Row) -> ModelCompleted:
    serialized_items = json.loads(row["items_json"])
    items = tuple(item_from_payload(value["kind"], value["payload"]) for value in serialized_items)
    return ModelCompleted(
        items,
        usage=ModelUsage(
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cached_tokens=row["cached_tokens"],
            reasoning_tokens=row["reasoning_tokens"],
            total_tokens=row["total_tokens"],
        ),
        provider_metadata=json.loads(row["metadata_json"]),
        end_turn=None if row["end_turn"] is None else bool(row["end_turn"]),
    )


def _result_from_json(value: str) -> ToolResult:
    raw = json.loads(value)
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
        display_content=raw.get("display_content"),
        content_items=tuple(content_from_payload(part) for part in raw.get("content_items", ())),
        code_mode_output=CodeModeOutput(raw["code_mode_output"]["value"])
        if "code_mode_output" in raw
        else None,
        attachments=tuple(ImageAttachment(**item) for item in raw.get("attachments", [])),
        state_update=ToolStateUpdate(
            plan=tuple(dict(item) for item in plan) if plan is not None else None,
            new_context_requested=raw.get("new_context_requested", False),
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
                    MessageRole.DEVELOPER,
                    item.summary,
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
