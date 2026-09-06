"""Versioned SQLite repository for threads, turns, items, and tool execution."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from corki.models.types import ModelCompleted, ModelUsage
from corki.protocol.ids import MessageId, ModelStepId, ThreadId, ToolCallId, TurnId
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ContextItem,
    ContextRole,
    ConversationItem,
    ReasoningItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    item_from_payload,
    item_kind,
    item_to_payload,
    items_from_messages,
)
from corki.protocol.messages import Message, MessageRole
from corki.protocol.tools import (
    ImageAttachment,
    ToolCall,
    ToolResult,
    ToolStateUpdate,
    tool_spec_from_payload,
)
from corki.sessions.models import TurnRecord


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
                    status TEXT NOT NULL,
                    result_json TEXT,
                    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at TEXT
                );
                """
            )
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
        await asyncio.to_thread(self._create_thread, thread_id, cwd)

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
        await asyncio.to_thread(self._save_turn, turn)

    async def latest_running_turn(self, thread_id: ThreadId) -> TurnRecord | None:
        return await asyncio.to_thread(self._latest_running_turn, thread_id)

    def _latest_running_turn(self, thread_id: ThreadId) -> TurnRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, thread_id, status, user_input, final_answer, error
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
        )

    def _save_turn(self, turn: TurnRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO turns(id, thread_id, status, user_input, final_answer, error)
                VALUES (?, ?, ?, ?, ?, ?)
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
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT status, result_json, thread_id, turn_id, tool_name
                FROM tool_executions WHERE call_id=?
                """,
                (str(call.id),),
            ).fetchone()
            if row is not None:
                if (
                    row["thread_id"] != str(thread_id)
                    or row["turn_id"] != str(turn_id)
                    or row["tool_name"] != call.name
                ):
                    return ToolResult(
                        call.id,
                        call.name,
                        "Tool call id collision; execution was refused.",
                        is_error=True,
                    )
                if row["status"] == "completed" and row["result_json"]:
                    return _result_from_json(row["result_json"])
                return ToolResult(
                    call.id,
                    call.name,
                    "Previous execution was interrupted; outcome is unknown and was not repeated.",
                    is_error=True,
                )
            connection.execute(
                """
                INSERT INTO tool_executions(call_id, thread_id, turn_id, tool_name, status)
                VALUES (?, ?, ?, ?, 'running')
                """,
                (str(call.id), str(thread_id), str(turn_id), call.name),
            )
        return None

    async def complete_tool_call(
        self, thread_id: ThreadId, turn_id: TurnId, result: ToolResult
    ) -> None:
        await asyncio.to_thread(self._complete_tool_call, thread_id, turn_id, result)

    def _complete_tool_call(self, thread_id: ThreadId, turn_id: TurnId, result: ToolResult) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE tool_executions
                SET status='completed', result_json=?, completed_at=CURRENT_TIMESTAMP
                WHERE call_id=? AND thread_id=? AND turn_id=?
                """,
                (
                    _result_to_json(result),
                    str(result.call_id),
                    str(thread_id),
                    str(turn_id),
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("tool execution ledger entry is missing or belongs elsewhere")

    async def load_model_step(
        self, thread_id: ThreadId, turn_id: TurnId, step_index: int
    ) -> ModelCompleted | None:
        return await asyncio.to_thread(self._load_model_step, thread_id, turn_id, step_index)

    def _load_model_step(
        self, thread_id: ThreadId, turn_id: TurnId, step_index: int
    ) -> ModelCompleted | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT input_tokens, output_tokens, cached_tokens, reasoning_tokens,
                       metadata_json, items_json, end_turn
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
            existing = connection.execute(
                """
                SELECT input_tokens, output_tokens, cached_tokens, reasoning_tokens,
                       metadata_json, items_json, end_turn
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

    async def close(self) -> None:
        return None


def _result_to_json(result: ToolResult) -> str:
    value = {
        "call_id": str(result.call_id),
        "tool_name": result.tool_name,
        "discovered_tools": [asdict(spec) for spec in result.discovered_tools],
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
        display_content=raw.get("display_content"),
        attachments=tuple(ImageAttachment(**item) for item in raw.get("attachments", [])),
        state_update=ToolStateUpdate(
            plan=tuple(dict(item) for item in plan) if plan is not None else None
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
        if isinstance(item, UserMessageItem):
            messages.append(
                Message(
                    MessageRole.USER,
                    item.content,
                    MessageId(str(item.id)),
                    item.turn_id,
                    attachments=item.attachments,
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
