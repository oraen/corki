"""Typed content projection reaches actual nested execution and durable results."""

import asyncio

import pytest
from test_code_mode_results import run_script

from corki.code_mode.service import CodeModeService
from corki.mcp.tools import MCPTool
from corki.tools import ToolRegistry

pytestmark = pytest.mark.skipif(not CodeModeService.available(), reason="install corki[code-mode]")


@pytest.mark.parametrize("mode", ["code_mode", "code_mode_only"])
def test_projected_resource_blocks_survive_nested_runtime_without_unknown_fields(tmp_path, mode):
    calls = []
    raw = {
        "content": [
            {"type": "text", "text": "before", "unknown": "DROP"},
            {
                "type": "resource",
                "resource": {"uri": "urn:fixture", "text": "selected", "blob": "DROP"},
            },
            {
                "type": "resource",
                "resource": {"uri": "urn:fixture", "text": False, "blob": "selected"},
            },
            {
                "type": "resource_link",
                "uri": "urn:fixture",
                "name": "fixture",
                "title": None,
                "icons": [{"src": "urn:icon", "unknown": "DROP"}],
            },
        ],
        "_meta": {"secret": "PRIVATE"},
    }

    class Client:
        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            return raw

    registry = ToolRegistry()
    registry.register(MCPTool("fixture", {"name": "read"}, Client()))
    content, ledger, request = asyncio.run(
        run_script(
            tmp_path,
            "const r = await tools.mcp__fixture__read({}); text(r);",
            registry=registry,
            mode=mode,
        )
    )
    assert len(calls) == 1
    assert "DROP" not in content and "PRIVATE" not in repr(request)
    result = ledger["mcp__fixture::read"]
    assert not result["is_error"]
    assert result["code_mode_output"]["value"] == {
        "content": [
            {"type": "text", "text": "before"},
            {"type": "resource", "resource": {"uri": "urn:fixture", "text": "selected"}},
            {"type": "resource", "resource": {"uri": "urn:fixture", "blob": "selected"}},
            {
                "type": "resource_link",
                "uri": "urn:fixture",
                "name": "fixture",
                "icons": [{"src": "urn:icon"}],
            },
        ]
    }
    assert raw["content"][1]["resource"]["blob"] == "DROP"
