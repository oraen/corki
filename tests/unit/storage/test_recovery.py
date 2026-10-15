import asyncio
import sqlite3
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from corki.models import ModelCompleted
from corki.models.types import ModelUsage
from corki.protocol.ids import new_thread_id, new_tool_call_id, new_turn_id
from corki.protocol.items import AssistantMessageItem, UserMessageItem, new_step_id
from corki.protocol.tools import ToolCall, ToolResult
from corki.storage import SQLiteSessionRepository, StorageIntegrityError


def test_model_step_and_items_commit_atomically_and_load_idempotently(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        assert stat.S_IMODE(repository.path.stat().st_mode) == 0o600
        thread_id, turn_id = new_thread_id(), new_turn_id()
        await repository.create_thread(thread_id, tmp_path)
        completed = ModelCompleted(
            (AssistantMessageItem("done", turn_id, new_step_id()),),
            ModelUsage(input_tokens=8, output_tokens=2),
            {"response_id": "response-1"},
        )

        await repository.commit_model_step(thread_id, turn_id, 0, completed)
        await repository.commit_model_step(thread_id, turn_id, 0, completed)
        cached = await repository.load_model_step(thread_id, turn_id, 0)

        assert cached == completed
        assert await repository.load_items(thread_id) == completed.items
        await repository.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("end_turn", [None, False, True])
def test_model_step_continuation_is_durable_and_part_of_idempotent_identity(
    tmp_path: Path, end_turn
) -> None:
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "continuation.db")
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        completed = ModelCompleted(
            (), provider_metadata={"end_turn": "provider-metadata"}, end_turn=end_turn
        )
        await repository.commit_model_step(thread, turn, 0, completed)
        await repository.commit_model_step(thread, turn, 0, completed)
        assert await repository.load_model_step(thread, turn, 0) == completed
        with pytest.raises(RuntimeError, match="conflicting model result"):
            await repository.commit_model_step(
                thread, turn, 0, replace(completed, end_turn=end_turn is not True)
            )
        assert await repository.load_model_step(thread, turn, 0) == completed
        await repository.close()

    asyncio.run(scenario())


def test_legacy_model_steps_gain_nullable_continuation_without_changing_old_results(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy-model.db"
    thread, turn = new_thread_id(), new_turn_id()
    with sqlite3.connect(database) as connection:
        connection.execute("""
            CREATE TABLE model_steps (
                step_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
                cached_tokens INTEGER NOT NULL, reasoning_tokens INTEGER NOT NULL,
                metadata_json TEXT NOT NULL, step_index INTEGER,
                items_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.execute(
            """
            INSERT INTO model_steps(step_id, thread_id, turn_id, input_tokens,
                output_tokens, cached_tokens, reasoning_tokens, metadata_json, step_index)
            VALUES ('old', ?, ?, 0, 0, 0, 0, '{}', 0)
        """,
            (str(thread), str(turn)),
        )

    async def scenario():
        repository = SQLiteSessionRepository(database)
        await repository.create_thread(thread, tmp_path)
        assert await repository.load_model_step(thread, turn, 0) == ModelCompleted(())
        await repository.commit_model_step(thread, turn, 0, ModelCompleted(()))
        await repository.commit_model_step(thread, turn, 1, ModelCompleted((), end_turn=False))
        await repository.close()
        reopened = SQLiteSessionRepository(database)
        assert (await reopened.load_model_step(thread, turn, 1)).end_turn is False
        assert (await reopened.load_model_step(thread, turn, 0)).end_turn is None
        await reopened.close()

    asyncio.run(scenario())


def test_item_append_is_idempotent_but_rejects_id_collision(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        first_thread, second_thread = new_thread_id(), new_thread_id()
        turn_id = new_turn_id()
        await repository.create_thread(first_thread, tmp_path)
        await repository.create_thread(second_thread, tmp_path)
        item = UserMessageItem("original", turn_id)

        await repository.append_items(first_thread, (item,))
        await repository.append_items(first_thread, (item,))
        assert await repository.load_items(first_thread) == (item,)

        with pytest.raises(StorageIntegrityError, match="id collision"):
            await repository.append_items(first_thread, (replace(item, content="changed"),))
        with pytest.raises(StorageIntegrityError, match="id collision"):
            await repository.append_items(second_thread, (item,))
        assert await repository.load_items(first_thread) == (item,)
        assert await repository.load_items(second_thread) == ()
        await repository.close()

    asyncio.run(scenario())


def test_tool_ledger_reuses_completed_result_and_refuses_id_collision(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        first_thread, second_thread, turn_id = (
            new_thread_id(),
            new_thread_id(),
            new_turn_id(),
        )
        await repository.create_thread(first_thread, tmp_path)
        await repository.create_thread(second_thread, tmp_path)
        call = ToolCall(new_tool_call_id(), "exec_command", {"cmd": "touch once"})
        assert await repository.claim_tool_call(first_thread, turn_id, call) is None
        result = ToolResult(call.id, call.name, "completed")
        await repository.complete_tool_call(first_thread, turn_id, result)

        assert await repository.claim_tool_call(first_thread, turn_id, call) == result
        collision = await repository.claim_tool_call(second_thread, turn_id, call)
        assert collision is not None
        assert collision.is_error
        assert "collision" in collision.content
        await repository.close()

    asyncio.run(scenario())


def test_legacy_message_schema_migrates_once(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    thread_id, turn_id = new_thread_id(), new_turn_id()
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY, cwd TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE messages (
                id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                sequence INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                tool_calls_json TEXT NOT NULL, tool_call_id TEXT, name TEXT,
                attachments_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO threads(id, cwd) VALUES (?, ?)", (str(thread_id), str(tmp_path))
        )
        connection.execute(
            """
            INSERT INTO messages VALUES (?, ?, ?, 0, 'user', ?, '[]', NULL, NULL, '[]', ?)
            """,
            ("message-1", str(thread_id), str(turn_id), "legacy text", "2026-01-01"),
        )

    first = SQLiteSessionRepository(path)
    items = asyncio.run(first.load_items(thread_id))
    second = SQLiteSessionRepository(path)
    repeated = asyncio.run(second.load_items(thread_id))

    assert len(items) == 1
    assert isinstance(items[0], UserMessageItem)
    assert items[0].content == "legacy text"
    assert repeated == items
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM schema_migrations "
                "WHERE name='legacy_messages_to_conversation_items_v1'"
            ).fetchone()[0]
            == 1
        )
        assert connection.execute("SELECT preview FROM threads").fetchone() == ("legacy text",)
