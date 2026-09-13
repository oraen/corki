"""Ordinary declaration 8 KB/64 KB limits, after inner parameter fallback."""

import pytest

from corki.mcp.exposure import apply_agent_budget
from corki.mcp.input_schema import json_bytes
from corki.mcp.tools import MCPTool
from corki.protocol.tools import ToolExposure


@pytest.mark.parametrize("server", ["docs", "long_service_name"])
@pytest.mark.parametrize("description", ["plain", "中文é" * 40])
def test_budget_counts_the_ordinary_wire_definitions(server, description):
    tool = MCPTool(
        server, {"name": "lookup", "description": description}, object(), agent_plugin=True
    )
    spec = tool.spec
    assert tool.model_spec_bytes() == max(
        json_bytes(spec.as_response_tool()), json_bytes(spec.as_chat_completion_tool())
    )


def sized_tool(name, size):
    definition = {
        "name": name,
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "string", "enum": [""]},
            },
        },
    }
    base = MCPTool("docs", definition, object(), agent_plugin=True)
    definition["inputSchema"]["properties"]["x"]["enum"] = ["x" * (size - base.model_spec_bytes())]
    result = MCPTool("docs", definition, object(), agent_plugin=True)
    assert result.model_spec_bytes() == size
    return result


def test_exact_declaration_and_cumulative_limits_and_frozen_handlers():
    tools = [sized_tool(f"t{i}", 8000) for i in range(9)]
    result = apply_agent_budget(tools)
    assert all(t.spec.exposure == ToolExposure.DIRECT for t in result[:8])
    assert result[8].spec.exposure == ToolExposure.HIDDEN
    assert tools[8].spec.exposure == ToolExposure.DIRECT
    assert result[8].mcp_omit_tools_from == ("direct", "deferred", "code_mode")
    assert tools[8].mcp_omit_tools_from is None
    assert result[8].remote_name == tools[8].remote_name


def test_oversized_declaration_does_not_consume_budget_or_stop_later_tools():
    large, small = sized_tool("large", 8001), sized_tool("small", 1000)
    ordinary = MCPTool("regular", {"name": "unlimited", "description": "x" * 65000}, object())
    result = apply_agent_budget([large, ordinary, small])
    assert [t.spec.exposure for t in result] == [
        ToolExposure.HIDDEN,
        ToolExposure.DIRECT,
        ToolExposure.DIRECT,
    ]
    assert result[1] is ordinary and result[2] is small
