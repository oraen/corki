"""A rounded decoded cache cannot authorize reuse for different wire arguments."""

import asyncio
import json
import sqlite3
from dataclasses import replace
from hashlib import sha256

import pytest

from corki.protocol.ids import new_thread_id, new_tool_call_id, new_turn_id
from corki.protocol.tools import ToolCall, ToolResult
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize(
    "raw,changed",
    [
        ('{"n":1.234567890123456781}', '{"n":1.234567890123456789}'),
        ('{"n":1e-999}', '{"n":2e-999}'),
        ('{"n":1}', '{"n":2}'),
        ('{"n":1}', '{"n":'),
    ],
)
def test_reopened_ledger_binds_exact_raw_and_decoded_cache(tmp_path, completed, raw, changed):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "numbers.db")
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        call = ToolCall(new_tool_call_id(), "mcp__docs::read", json.loads(raw), raw_arguments=raw)
        assert await repository.claim_tool_call(thread, turn, call) is None
        if completed:
            await repository.complete_tool_call(thread, turn, ToolResult(call.id, call.name, "one"))
        repository = SQLiteSessionRepository(repository.path)
        collision = await repository.claim_tool_call(
            thread, turn, replace(call, raw_arguments=changed)
        )
        assert collision.is_error and "collision" in collision.content
        unchanged = await repository.claim_tool_call(thread, turn, call)
        assert unchanged.content == "one" if completed else "unknown" in unchanged.content
        same = await repository.claim_tool_call(
            thread, turn, replace(call, raw_arguments=" \n" + raw + " \t")
        )
        assert same == unchanged

    asyncio.run(scenario())


def test_old_lossy_fingerprint_is_not_backfilled_or_reexecuted(tmp_path):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "legacy.db")
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        raw = '{"n":1.234567890123456789}'
        call = ToolCall(new_tool_call_id(), "mcp__docs::read", json.loads(raw), raw_arguments=raw)
        await repository.claim_tool_call(thread, turn, call)
        await repository.complete_tool_call(thread, turn, ToolResult(call.id, call.name, "legacy"))
        old_hash = sha256(
            json.dumps(
                {"parsed": dict(call.arguments)}, sort_keys=True, separators=(",", ":")
            ).encode("ascii")
        ).hexdigest()
        with sqlite3.connect(repository.path) as db:
            db.execute("UPDATE tool_executions SET arguments_sha256=?", (old_hash,))
        reopened = SQLiteSessionRepository(repository.path)
        result = await reopened.claim_tool_call(thread, turn, call)
        assert result.is_error and "collision" in result.content
        with sqlite3.connect(repository.path) as db:
            assert db.execute("SELECT arguments_sha256,status FROM tool_executions").fetchone() == (
                old_hash,
                "completed",
            )

    asyncio.run(scenario())
