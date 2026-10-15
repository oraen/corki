"""Recovery must match call payloads, and completion records are immutable."""

import asyncio
import sqlite3
from dataclasses import replace

import pytest

from corki.protocol.ids import new_thread_id, new_tool_call_id, new_turn_id
from corki.protocol.items import ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall, ToolResult
from corki.storage import SQLiteSessionRepository
from corki.storage.sqlite import StorageIntegrityError


@pytest.mark.parametrize("status", ["running", "completed", "reopened"])
def test_same_call_id_with_different_arguments_is_a_collision(tmp_path, status):
    async def scenario():
        path = tmp_path / "ledger.db"
        repository = SQLiteSessionRepository(path)
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        call = ToolCall(new_tool_call_id(), "write", {"path": "one.txt"})
        assert await repository.claim_tool_call(thread, turn, call) is None
        if status != "running":
            await repository.complete_tool_call(thread, turn, ToolResult(call.id, call.name, "one"))
        if status == "reopened":
            repository = SQLiteSessionRepository(path)
        collision = await repository.claim_tool_call(
            thread, turn, replace(call, arguments={"path": "two.txt"})
        )
        assert collision.is_error and "collision" in collision.content
        original = await repository.claim_tool_call(thread, turn, call)
        assert original.content == "one" if status != "running" else "unknown" in original.content

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["content", "name"])
def test_completed_result_cannot_be_rewritten_or_assigned_to_another_tool(tmp_path, change):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "ledger.db")
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        call = ToolCall(new_tool_call_id(), "write", {})
        await repository.claim_tool_call(thread, turn, call)
        result = ToolResult(call.id, call.name, "original")
        await repository.complete_tool_call(thread, turn, result)
        await repository.complete_tool_call(thread, turn, result)
        changed = (
            replace(result, content="different")
            if change == "content"
            else replace(result, tool_name="other")
        )
        with pytest.raises(StorageIntegrityError):
            await repository.complete_tool_call(thread, turn, changed)
        assert await repository.claim_tool_call(thread, turn, call) == result

    asyncio.run(scenario())


def test_missing_legacy_arguments_are_not_assumed_to_match(tmp_path):
    async def scenario():
        database = tmp_path / "ledger.db"
        repository = SQLiteSessionRepository(database)
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        call = ToolCall(new_tool_call_id(), "write", {"path": "one.txt"})
        await repository.claim_tool_call(thread, turn, call)
        await repository.complete_tool_call(thread, turn, ToolResult(call.id, call.name, "legacy"))
        with sqlite3.connect(database) as connection:
            columns = [row[1] for row in connection.execute("PRAGMA table_info(tool_executions)")]
            if "arguments_sha256" in columns:
                connection.execute("ALTER TABLE tool_executions DROP COLUMN arguments_sha256")
        reopened = SQLiteSessionRepository(database)
        unknown = await reopened.claim_tool_call(thread, turn, call)
        assert unknown.is_error and "unverified" in unknown.content
        with sqlite3.connect(database) as connection:
            assert (
                connection.execute("SELECT status FROM tool_executions").fetchone()[0]
                == "completed"
            )

    asyncio.run(scenario())


def test_equivalent_json_arguments_reuse_result_but_invalid_raw_arguments_do_not(tmp_path):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "ledger.db")
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        call = ToolCall(
            new_tool_call_id(), "write", {"a": 1, "b": [2]}, raw_arguments='{"a":1,"b":[2]}'
        )
        await repository.claim_tool_call(thread, turn, call)
        result = ToolResult(call.id, call.name, "ok")
        await repository.complete_tool_call(thread, turn, result)
        same = replace(call, arguments={"b": [2], "a": 1}, raw_arguments='{ "b": [2], "a": 1 }')
        assert await repository.claim_tool_call(thread, turn, same) == result
        bad = ToolCall(
            new_tool_call_id(), "write", None, raw_arguments="{bad", parse_error="bad JSON"
        )
        await repository.claim_tool_call(thread, turn, bad)
        await repository.complete_tool_call(
            thread, turn, ToolResult(bad.id, bad.name, "bad JSON", is_error=True)
        )
        collision = await repository.claim_tool_call(
            thread, turn, replace(bad, raw_arguments="{other")
        )
        assert collision.is_error and "collision" in collision.content

    asyncio.run(scenario())


def test_concurrent_conflicting_completions_have_one_winner(tmp_path):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "ledger.db")
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        call = ToolCall(new_tool_call_id(), "write", {})
        await repository.claim_tool_call(thread, turn, call)
        results = (ToolResult(call.id, call.name, "one"), ToolResult(call.id, call.name, "two"))
        outcomes = await asyncio.gather(
            *(repository.complete_tool_call(thread, turn, result) for result in results),
            return_exceptions=True,
        )
        assert sum(value is None for value in outcomes) == 1
        assert sum(isinstance(value, StorageIntegrityError) for value in outcomes) == 1
        cached = await repository.claim_tool_call(thread, turn, call)
        assert cached == results[outcomes.index(None)]

    asyncio.run(scenario())


def test_later_history_cannot_be_used_to_guess_legacy_execution_arguments(tmp_path):
    async def scenario():
        database = tmp_path / "ledger.db"
        repository = SQLiteSessionRepository(database)
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        call = ToolCall(new_tool_call_id(), "write", {"path": "one.txt"})
        await repository.claim_tool_call(thread, turn, call)
        original = ToolResult(call.id, call.name, "one")
        await repository.complete_tool_call(thread, turn, original)
        with sqlite3.connect(database) as connection:
            connection.execute("ALTER TABLE tool_executions DROP COLUMN arguments_sha256")
        reopened = SQLiteSessionRepository(database)
        later_call = replace(call, arguments={"path": "two.txt"})
        await reopened.append_items(thread, (ToolCallItem(later_call, turn, new_step_id()),))
        wrong = await reopened.claim_tool_call(thread, turn, later_call)
        assert wrong.is_error and "unverified" in wrong.content

    asyncio.run(scenario())
