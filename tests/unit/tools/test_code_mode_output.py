"""Strict JSON overrides, immutable ledger compatibility and private MCP fields."""

import asyncio
import json
import sqlite3
from dataclasses import replace

import pytest

from corki.mcp.tools import MCPTool
from corki.protocol.ids import new_thread_id, new_tool_call_id, new_turn_id
from corki.protocol.tools import CodeModeOutput, ToolCall, ToolResult, ToolSpec
from corki.storage import SQLiteSessionRepository
from corki.storage.sqlite import StorageIntegrityError
from corki.tools import ToolContext, ToolExecutor, ToolRegistry


async def execute_value(tmp_path, value, *, wrapped=True):
    class Tool:
        spec = ToolSpec("typed", "fixture", {"type": "object"})

        async def execute(self, call, context):
            return ToolResult(
                call.id,
                call.name,
                "diagnostic",
                code_mode_output=CodeModeOutput(value) if wrapped else value,
            )

    registry = ToolRegistry()
    registry.register(Tool())
    return await ToolExecutor(registry, output_char_budget=1000).execute(
        ToolCall(new_tool_call_id(), "typed", {}), ToolContext(tmp_path)
    )


@pytest.mark.parametrize("value", [None, False, 0, 1.5, "", [1, None], {"rows": [{"v": 42}]}])
def test_json_override_is_durable_and_completed_result_stays_immutable(tmp_path, value):
    async def scenario():
        result = await execute_value(tmp_path, value)
        assert not result.is_error
        assert result.code_mode_output == CodeModeOutput(value)
        repository = SQLiteSessionRepository(tmp_path / "ledger.db")
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        call = ToolCall(result.call_id, result.tool_name, {})
        await repository.claim_tool_call(thread, turn, call)
        await repository.complete_tool_call(thread, turn, result)
        reopened = SQLiteSessionRepository(repository.path)
        loaded = await reopened.claim_tool_call(thread, turn, call)
        assert loaded == result
        await reopened.complete_tool_call(thread, turn, loaded)
        with pytest.raises(StorageIntegrityError, match="(?i)immutable|different|completed"):
            await reopened.complete_tool_call(
                thread, turn, replace(result, code_mode_output=CodeModeOutput("replacement"))
            )
        assert await reopened.claim_tool_call(thread, turn, call) == result

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), {1: "bad key"}, (1, 2), {"bad": object()}, "\ud800"]
)
def test_invalid_json_override_is_bounded_error_observation(tmp_path, value):
    result = asyncio.run(execute_value(tmp_path, value))
    assert result.is_error and result.code_mode_output is None
    assert result.dispatch_error
    assert len(result.content) <= 1000


def test_invalid_wrapper_cycles_and_depth_are_observations(tmp_path):
    cycle = []
    cycle.append(cycle)
    for value, wrapped in [(cycle, True), ({"unwrapped": 42}, False)]:
        result = asyncio.run(execute_value(tmp_path, value, wrapped=wrapped))
        assert result.is_error and result.code_mode_output is None


def test_oversized_json_is_rejected_without_changing_its_meaning(tmp_path, monkeypatch):
    monkeypatch.setattr("corki.protocol.tools.MAX_CODE_MODE_RESULT_BYTES", 64)
    result = asyncio.run(execute_value(tmp_path, {"value": "中" * 30}))
    assert result.is_error and "byte limit" in result.content
    assert result.code_mode_output is None


def test_normalized_override_does_not_share_mutable_handler_value(tmp_path):
    raw = {"rows": [{"v": 42}]}
    result = asyncio.run(execute_value(tmp_path, raw))
    raw["rows"][0]["v"] = 999
    assert result.code_mode_output.value == {"rows": [{"v": 42}]}


def test_shared_python_graph_cannot_cause_unbounded_validation(tmp_path, monkeypatch):
    monkeypatch.setattr("corki.protocol.tools.MAX_CODE_MODE_RESULT_NODES", 32)
    value = []
    for _ in range(32):
        value = [value, value]
    result = asyncio.run(execute_value(tmp_path, value))
    assert result.is_error and "node limit" in result.content


@pytest.mark.parametrize("raw", [{"content": None}, {"content": [], "isError": "false"}])
def test_invalid_mcp_envelope_is_an_observation(tmp_path, raw):
    class Client:
        async def call_tool(self, name, arguments):
            return raw

    async def scenario():
        tool = MCPTool("fixture", {"name": "read"}, Client())
        registry = ToolRegistry()
        registry.register(tool)
        result = await ToolExecutor(registry, output_char_budget=1000).execute(
            ToolCall(new_tool_call_id(), tool.spec.name, {}), ToolContext(tmp_path)
        )
        assert result.is_error and "MCP result." in result.content
        assert not result.dispatch_error
        assert result.code_mode_output.value["isError"] is True
        assert "MCPProtocolError" in result.code_mode_output.value["content"][0]["text"]

    asyncio.run(scenario())


def test_absent_override_keeps_old_ledger_payload_byte_compatible(tmp_path):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "ledger.db")
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        call = ToolCall(new_tool_call_id(), "fixture", {})
        result = ToolResult(call.id, call.name, "null")
        old = json.dumps(
            {
                "call_id": str(call.id),
                "tool_name": call.name,
                "discovered_tools": [],
                "content": "null",
                "is_error": False,
                "display_content": None,
                "attachments": [],
                "plan": None,
            },
            ensure_ascii=False,
        )
        await repository.claim_tool_call(thread, turn, call)
        await repository.complete_tool_call(thread, turn, result)
        with sqlite3.connect(repository.path) as db:
            assert db.execute("SELECT result_json FROM tool_executions").fetchone()[0] == old
        reopened = SQLiteSessionRepository(repository.path)
        loaded = await reopened.claim_tool_call(thread, turn, call)
        assert loaded.code_mode_output is None
        await reopened.complete_tool_call(thread, turn, loaded)

    asyncio.run(scenario())


@pytest.mark.parametrize("empty", [False, True])
def test_mcp_direct_observation_never_exposes_private_result_metadata(tmp_path, empty):
    raw = {
        "content": [] if empty else [{"type": "text", "text": "public"}],
        "_meta": {"secret": "PRIVATE"},
        "unknownTransportField": "PRIVATE",
    }

    class Client:
        async def call_tool(self, name, arguments):
            return raw

    async def scenario():
        tool = MCPTool("fixture", {"name": "read"}, Client())
        registry = ToolRegistry()
        registry.register(tool)
        result = await ToolExecutor(registry, output_char_budget=1000).execute(
            ToolCall(new_tool_call_id(), tool.spec.name, {}), ToolContext(tmp_path)
        )
        assert not result.is_error
        assert "PRIVATE" not in result.content + result.display_content
        assert result.code_mode_output.value == {"content": raw["content"]}
        raw["content"].append({"type": "text", "text": "late"})
        assert "late" not in json.dumps(result.code_mode_output.value)

    asyncio.run(scenario())
