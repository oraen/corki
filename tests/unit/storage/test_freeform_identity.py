"""Raw call identity and old immutable JSON payload compatibility."""

import asyncio
import json
import sqlite3
from dataclasses import replace
from hashlib import sha256

import pytest

from corki.protocol.ids import new_thread_id, new_tool_call_id, new_turn_id
from corki.protocol.items import ToolCallItem, ToolResultItem, item_to_payload, new_step_id
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize("change", ["source", "kind", "parse_error"])
def test_raw_call_cannot_reuse_a_different_durable_claim(tmp_path, change):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "ledger.db")
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        call = ToolCall(new_tool_call_id(), "raw", None, raw_arguments="{}", input_kind="freeform")
        await repository.claim_tool_call(thread, turn, call)
        result = ToolResult(call.id, call.name, "executed")
        await repository.complete_tool_call(thread, turn, result)
        reopened = SQLiteSessionRepository(repository.path)
        assert await reopened.claim_tool_call(thread, turn, call) == result
        changed = {
            "source": {"raw_arguments": " {}"},
            "kind": {"input_kind": "json"},
            "parse_error": {"parse_error": "invalid"},
        }[change]
        collision = await reopened.claim_tool_call(thread, turn, replace(call, **changed))
        assert collision.is_error and "collision" in collision.content

    asyncio.run(scenario())


def test_old_json_history_definition_and_ledger_are_still_idempotent(tmp_path):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "old.db")
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        call = ToolCall(new_tool_call_id(), "tool_search", {"query": "probe"})
        spec = ToolSpec("probe", "fixture", {"type": "object"})
        result = ToolResult(call.id, call.name, "found", discovered_tools=(spec,))
        items = (
            ToolCallItem(call, turn, new_step_id()),
            ToolResultItem(call.id, call.name, "found", turn, discovered_tools=(spec,)),
        )
        await repository.append_items(thread, items)
        await repository.claim_tool_call(thread, turn, call)
        await repository.complete_tool_call(thread, turn, result)
        with sqlite3.connect(repository.path) as connection:
            rows = connection.execute(
                "SELECT payload_json FROM conversation_items ORDER BY sequence"
            ).fetchall()
            old_payloads = [json.loads(row[0]) for row in rows]
            assert "input_kind" not in old_payloads[0]["call"]
            assert "input_kind" not in old_payloads[1]
            assert "input_kind" not in old_payloads[1]["discovered_tools"][0]
            assert "freeform_format" not in old_payloads[1]["discovered_tools"][0]
            expected = sha256(
                json.dumps(
                    {"parsed": {"query": "probe"}}, sort_keys=True, separators=(",", ":")
                ).encode("ascii")
            ).hexdigest()
            assert (
                connection.execute("SELECT arguments_sha256 FROM tool_executions").fetchone()[0]
                == expected
            )
        reopened = SQLiteSessionRepository(repository.path)
        loaded = await reopened.load_items(thread)
        await reopened.append_items(thread, loaded)
        await reopened.complete_tool_call(thread, turn, result)
        assert json.loads(json.dumps([item_to_payload(item) for item in loaded])) == old_payloads
        assert await reopened.claim_tool_call(thread, turn, call) == result

    asyncio.run(scenario())
