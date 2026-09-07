import asyncio
import json
import sqlite3

import pytest

from corki.protocol.ids import new_thread_id, new_tool_call_id, new_turn_id
from corki.protocol.tools import ToolCall, ToolResult
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize("dispatch_error", [False, True])
def test_error_channel_survives_reopen_without_rewriting_legacy_rows(tmp_path, dispatch_error):
    async def scenario():
        path = tmp_path / "ledger.db"
        repository = SQLiteSessionRepository(path)
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        call = ToolCall(new_tool_call_id(), "probe", {})
        assert await repository.claim_tool_call(thread, turn, call) is None
        result = ToolResult(
            call.id, call.name, "failure", is_error=True, dispatch_error=dispatch_error
        )
        await repository.complete_tool_call(thread, turn, result)
        with sqlite3.connect(path) as db:
            before = db.execute("SELECT result_json FROM tool_executions").fetchone()[0]
        assert ("dispatch_error" in json.loads(before)) == dispatch_error
        reopened = SQLiteSessionRepository(path)
        cached = await reopened.claim_tool_call(thread, turn, call)
        assert cached == result
        await reopened.complete_tool_call(thread, turn, cached)
        with sqlite3.connect(path) as db:
            after = db.execute("SELECT result_json FROM tool_executions").fetchone()[0]
        assert before == after, "absent legacy field must not be backfilled into immutable results"

    asyncio.run(scenario())
