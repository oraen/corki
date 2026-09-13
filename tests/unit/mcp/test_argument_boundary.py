"""Schema hints never authorize or reject remote business arguments."""

import asyncio

import pytest

from corki.config import MCPServerSettings
from corki.mcp.approvals import MCPToolAnnotations, MCPToolApprovals
from corki.mcp.client import MCPClient
from corki.mcp.tools import MCPTool
from corki.protocol.ids import new_tool_call_id
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools import ToolExecutor, ToolRegistry
from corki.tools.base import ToolContext


@pytest.mark.parametrize(
    "raw,decoded,expected",
    [
        ('{"unknown":[1,true]}', {}, {"unknown": [1, True]}),
        ("", {"x": "outside advertised type"}, {"x": "outside advertised type"}),
        ("", None, None),
        (" \n\t", None, None),
        ("{}", None, {}),
        ("", {"n": 2**100}, {"n": 2**100}),
    ],
)
def test_remote_object_or_omitted_arguments_bypass_schema(tmp_path, raw, decoded, expected):
    async def scenario():
        calls = []

        class Client:
            async def call_tool(self, name, arguments):
                calls.append(arguments)
                return {"content": []}

        tool = MCPTool(
            "docs",
            {
                "name": "read",
                "inputSchema": {
                    "type": "object",
                    "properties": {"x": {"type": "integer"}},
                    "required": ["x"],
                    "additionalProperties": False,
                },
            },
            Client(),
        )
        registry = ToolRegistry()
        registry.register(tool)
        call = ToolCall(new_tool_call_id(), tool.spec.name, decoded, raw_arguments=raw)
        result = await ToolExecutor(registry, output_char_budget=1000).execute(
            call, ToolContext(cwd=tmp_path)
        )
        assert not result.is_error, result.content
        assert calls == [expected]
        assert call.arguments == decoded and call.raw_arguments == raw

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "raw", ["null", "[]", "true", '"text"', "{", '{"x":NaN}', '{"x":"\\ud800"}']
)
def test_invalid_arguments_are_mcp_error_values_without_remote_effects(tmp_path, raw):
    async def scenario():
        class Client:
            async def call_tool(self, *args):
                raise AssertionError("invalid input reached remote transport")

        tool = MCPTool("docs", {"name": "read"}, Client())
        registry = ToolRegistry()
        registry.register(tool)
        call = ToolCall(
            new_tool_call_id(),
            tool.spec.name,
            None,
            raw_arguments=raw,
            parse_error="provider parse error",
        )
        result = await ToolExecutor(registry, output_char_budget=1000).execute(
            call, ToolContext(cwd=tmp_path)
        )
        assert result.is_error
        assert result.code_mode_output.value["isError"] is True
        assert "arguments" in result.content
        assert "invalid input reached" not in result.content

    asyncio.run(scenario())


def test_other_tools_still_enforce_advertised_schema(tmp_path):
    async def scenario():
        class Tool:
            spec = ToolSpec("local", "Local", {"type": "object", "required": ["x"]})

            async def execute(self, call, context):
                return ToolResult(call.id, call.name, "must not execute")

        registry = ToolRegistry()
        registry.register(Tool())
        result = await ToolExecutor(registry, output_char_budget=1000).execute(
            ToolCall(new_tool_call_id(), "local", {}), ToolContext(cwd=tmp_path)
        )
        assert result.is_error and "must not execute" not in result.content

    asyncio.run(scenario())


def test_omitted_arguments_stay_omitted_on_wire_and_still_require_approval():
    async def scenario():
        requests, reviews = [], []

        class Client(MCPClient):
            async def start(self):
                pass

            async def aclose(self):
                pass

            async def _exchange(self, message):
                raise AssertionError(message)

            async def _send_notification(self, message):
                raise AssertionError(message)

            async def request(self, method, params):
                requests.append((method, params))
                return {"content": []}

        class Router:
            async def request_tool_approval(self, server, payload):
                reviews.append(payload)
                return {"action": "accept"}

        settings = MCPServerSettings(
            "docs", "http", url="https://arguments.test", default_tools_approval_mode="prompt"
        )
        approvals = MCPToolApprovals("on-request", Router())
        await approvals.check(settings, "read", None, MCPToolAnnotations())
        assert reviews[0]["_meta"]["tool_params"] is None
        client = Client(settings)
        await client.call_tool("read", None)
        await client.call_tool("read", {})
        assert requests == [
            ("tools/call", {"name": "read"}),
            ("tools/call", {"name": "read", "arguments": {}}),
        ]

    asyncio.run(scenario())
