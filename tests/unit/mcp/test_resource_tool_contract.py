"""Model resource arguments and serialized output follow the native handler."""

import asyncio
import json

import pytest

from corki.context.hosted_output import truncate_output_text
from corki.mcp.resources import MCPReadResourceTool, MCPResourceInventoryTool
from corki.protocol.ids import new_tool_call_id
from corki.protocol.tools import ToolCall
from corki.protocol.truncation import TruncationPolicy
from corki.protocol.wire_numbers import dumps_wire
from corki.tools import ToolContext, ToolExecutor, ToolRegistry


@pytest.mark.parametrize(
    "raw",
    ["", "  \n", "null", "{}", "[]", "[null]", '{"unknown":1}', '{"server":null,"cursor":null}'],
)
def test_empty_resource_arguments_default_to_all_servers(tmp_path, raw):
    calls = []

    class Manager:
        async def list_capability(self, kind, server, cursor):
            calls.append((kind, server, cursor))
            return {"resources": []}

    async def scenario():
        registry = ToolRegistry()
        registry.register(MCPResourceInventoryTool(Manager(), "resources"))
        result = await ToolExecutor(registry, output_char_budget=10_000).execute(
            ToolCall(new_tool_call_id(), "list_mcp_resources", None, raw_arguments=raw),
            ToolContext(cwd=tmp_path),
        )
        assert not result.is_error
        assert json.loads(result.content) == {"resources": []}
        assert calls == [("resources", None, None)]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "raw",
    [
        '{"server":false}',
        '{"cursor":"next"}',
        '[null,"next"]',
        '{"server":"docs","cursor":1}',
        '["a","b","c"]',
    ],
)
def test_invalid_resource_arguments_fail_before_remote_call(tmp_path, raw):
    class Manager:
        async def list_capability(self, *args):
            raise AssertionError("invalid arguments reached manager")

    async def scenario():
        registry = ToolRegistry()
        registry.register(MCPResourceInventoryTool(Manager(), "resources"))
        result = await ToolExecutor(registry, output_char_budget=10_000).execute(
            ToolCall(new_tool_call_id(), "list_mcp_resources", None, raw_arguments=raw),
            ToolContext(cwd=tmp_path),
        )
        assert result.is_error
        assert "reached manager" not in result.content

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["resources", "templates", "read"])
@pytest.mark.parametrize("mode", ["bytes", "tokens"])
def test_resource_payload_is_bounded_before_ledger_and_nested_consumers(tmp_path, kind, mode):
    body = "HEAD" + "界" * 1000 + "TAIL"
    field = {"resources": "resources", "templates": "resourceTemplates", "read": "contents"}[kind]
    value = {field: [{"text": body}]}

    class Manager:
        async def list_capability(self, kind, server, cursor):
            assert server == "docs" and cursor == "next"
            return value

        async def read_resource(self, server, uri):
            assert (server, uri) == ("docs", "fixture:x")
            return value

    async def scenario():
        registry = ToolRegistry()
        tool = (
            MCPReadResourceTool(Manager())
            if kind == "read"
            else MCPResourceInventoryTool(Manager(), kind)
        )
        registry.register(tool)
        raw = (
            '[" docs "," fixture:x "]'
            if kind == "read"
            else '{"server":"old","server":" docs ","cursor":" next ","ignored":true}'
        )
        policy = TruncationPolicy(mode, 100)
        result = await ToolExecutor(registry, output_char_budget=10_000).execute(
            ToolCall(new_tool_call_id(), tool.spec.name, None, raw_arguments=raw),
            ToolContext(cwd=tmp_path, model_output_policy=policy),
        )
        payload = {"server": "docs", "uri": "fixture:x", **value} if kind == "read" else value
        assert not result.is_error
        assert result.content == truncate_output_text(
            dumps_wire(payload), policy.history_allowance()
        )
        assert "truncated" in result.content and "TAIL" in result.content
        assert result.legacy_output_char_budget is None

    asyncio.run(scenario())
