"""The RAM connection preserves atomic claim and rollback semantics across workers."""

import asyncio

import pytest

from corki.protocol.ids import new_thread_id, new_tool_call_id, new_turn_id
from corki.protocol.tools import ToolCall, ToolResult
from corki.sessions.models import TurnRecord, TurnStatus
from corki.storage.volatile import VolatileSessionRepository


def test_volatile_parallel_claims_have_one_owner_and_transactions_roll_back(tmp_path):
    async def scenario():
        repository = VolatileSessionRepository()
        thread, turn = new_thread_id(), new_turn_id()
        call = ToolCall(new_tool_call_id(), "effect", {"value": 1})
        try:
            await repository.create_thread(thread, tmp_path)
            await repository.save_turn(TurnRecord(turn, thread, TurnStatus.RUNNING, "request"))
            outcomes = await asyncio.gather(
                *(repository.claim_tool_call(thread, turn, call) for _ in range(20))
            )
            assert outcomes.count(None) == 1
            assert all(
                outcome is None or (outcome.is_error and "outcome is unknown" in outcome.content)
                for outcome in outcomes
            )
            result = ToolResult(call.id, call.name, "performed once")
            await repository.complete_tool_call(thread, turn, result)
            assert await repository.claim_tool_call(thread, turn, call) == result
            with (
                pytest.raises(ValueError, match="rollback"),
                repository._connect() as connection,
            ):
                connection.execute("UPDATE threads SET source='exec'")
                raise ValueError("rollback fixture")
            assert (await repository.load_thread_source(thread)).storage_value == "vscode"
            with repository._connect() as connection:
                assert connection.execute("PRAGMA database_list").fetchall()[0][2] == ""
                assert connection.execute("PRAGMA temp_store").fetchone()[0] == 2
        finally:
            await repository.close()
        assert repository._closed

    asyncio.run(scenario())
