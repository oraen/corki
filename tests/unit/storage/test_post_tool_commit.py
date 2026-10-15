"""Tool facts and the eligible Post batch commit or roll back together."""

import asyncio
import sqlite3

import pytest

from corki.protocol.ids import new_thread_id, new_tool_call_id, new_turn_id
from corki.protocol.tools import ToolCall, ToolResult
from corki.storage import SQLiteSessionRepository
from corki.storage.sqlite import StorageIntegrityError
from corki.storage.volatile import VolatileSessionRepository


@pytest.mark.parametrize("volatile", [False, True])
@pytest.mark.parametrize("failure", ["batch", "tool", None])
def test_atomic_post_tool_commit(tmp_path, volatile, failure):
    async def scenario():
        repository = (
            VolatileSessionRepository()
            if volatile
            else SQLiteSessionRepository(tmp_path / "atomic.db")
        )
        thread, turn = new_thread_id(), new_turn_id()
        call = ToolCall(new_tool_call_id(), "probe", {})
        result = ToolResult(call.id, call.name, "EXECUTED")
        key = f"post_tool_use:{thread}:{turn}:{call.id}:"
        snapshot = {"commands": [{"key": "trusted"}]}
        try:
            await repository.create_thread(thread, tmp_path)
            await repository.claim_tool_call(thread, turn, call)
            if failure:
                statement = (
                    "BEFORE INSERT ON hook_batches"
                    if failure == "batch"
                    else "BEFORE UPDATE ON tool_executions"
                )
                with repository._connect() as connection:
                    connection.execute(
                        f"CREATE TRIGGER reject_commit {statement} "
                        "BEGIN SELECT RAISE(ABORT, 'injected commit failure'); END"
                    )
                with pytest.raises(sqlite3.IntegrityError, match="injected"):
                    await repository.complete_tool_call(thread, turn, result, (key, snapshot))
                assert await repository.load_hook_batch(thread, turn, key) is None
                assert "unknown" in (await repository.claim_tool_call(thread, turn, call)).content
                with repository._connect() as connection:
                    connection.execute("DROP TRIGGER reject_commit")
            await repository.complete_tool_call(thread, turn, result, (key, snapshot))
            await repository.complete_tool_call(thread, turn, result, (key, snapshot))
            assert await repository.claim_tool_call(thread, turn, call) == result
            assert await repository.load_hook_batch(thread, turn, key) == (snapshot, {})
            with pytest.raises(StorageIntegrityError, match="collision"):
                await repository.complete_tool_call(thread, turn, result, (key, {"commands": []}))
            assert await repository.load_hook_batch(thread, turn, key) == (snapshot, {})
        finally:
            await repository.close()

    asyncio.run(scenario())
