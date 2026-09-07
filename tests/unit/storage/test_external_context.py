import asyncio
import json
import sqlite3

import pytest

from corki.protocol.ids import new_thread_id, new_tool_call_id, new_turn_id
from corki.protocol.items import ToolResultItem, item_from_payload, item_to_payload
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolExecutor, ToolRegistry


@pytest.mark.parametrize("flag", [False, True])
def test_external_context_survives_history_and_immutable_ledger_reopen(tmp_path, flag):
    async def scenario():
        path = tmp_path / "sessions.db"
        repository = SQLiteSessionRepository(path)
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        call = ToolCall(new_tool_call_id(), "probe", {})
        result = ToolResult(call.id, call.name, "data", contains_external_context=flag)
        assert await repository.claim_tool_call(thread, turn, call) is None
        await repository.complete_tool_call(thread, turn, result)
        item = ToolResultItem(call.id, call.name, "data", turn, contains_external_context=flag)
        payload = item_to_payload(item)
        assert ("contains_external_context" in payload) == flag
        assert item_from_payload("tool_result", payload) == item
        await repository.append_items(thread, (item,))
        with sqlite3.connect(path) as db:
            before = db.execute("SELECT result_json FROM tool_executions").fetchone()[0]
        assert ("contains_external_context" in json.loads(before)) == flag
        reopened = SQLiteSessionRepository(path)
        cached = await reopened.claim_tool_call(thread, turn, call)
        assert cached == result
        assert (await reopened.load_items(thread))[-1] == item
        await reopened.complete_tool_call(thread, turn, cached)
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT result_json FROM tool_executions").fetchone()[0] == before

    asyncio.run(scenario())


@pytest.mark.parametrize("flag", [1, "true", None])
def test_executor_rejects_non_boolean_external_flag(flag):
    call = ToolCall(new_tool_call_id(), "probe", {})
    result = ToolResult(call.id, call.name, "data", contains_external_context=flag)
    executor = ToolExecutor(ToolRegistry(), output_char_budget=1000)
    with pytest.raises(ValueError, match="contains_external_context"):
        executor._normalize_result(
            call, result, ToolSpec("probe", "probe", {"type": "object"}), is_search=False
        )
