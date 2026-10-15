"""Raw duplicate fields select the correct resource through HTTP and nested Runtime."""

import asyncio
import json

import httpx
import pytest
from test_code_mode_results import run_script

from corki.code_mode.service import CodeModeService
from corki.config import MCPServerSettings
from corki.mcp.client import HttpMCPClient
from corki.mcp.tools import MCPTool
from corki.tools import ToolRegistry

pytestmark = pytest.mark.skipif(not CodeModeService.available(), reason="install corki[code-mode]")


@pytest.mark.parametrize("mode", ["code_mode", "code_mode_only"])
def test_http_duplicate_text_selects_blob_before_nested_output_and_ledger(tmp_path, mode):
    async def scenario():
        calls = []

        def respond(request):
            packet = json.loads(request.content)
            if packet["method"] == "notifications/initialized":
                return httpx.Response(202)
            if packet["method"] == "initialize":
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": packet["id"],
                        "result": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "serverInfo": {"name": "fixture", "version": "1"},
                        },
                    },
                )
            assert packet["method"] == "tools/call"
            calls.append(packet)
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=(
                    '{"jsonrpc":"2.0","id":'
                    + str(packet["id"])
                    + ',"result":{"content":[{"type":"resource","resource":'
                    '{"uri":"x","text":"DROP","text":"WRONG","blob":"selected"}}]}}'
                ).encode(),
            )

        client = HttpMCPClient(
            MCPServerSettings("fixture", "http", url="https://fixture.invalid/mcp"),
            transport=httpx.MockTransport(respond),
        )
        try:
            await client.initialize()
            registry = ToolRegistry()
            registry.register(MCPTool("fixture", {"name": "read"}, client))
            output, ledger, request = await run_script(
                tmp_path,
                "const r = await tools.mcp__fixture__read({}); text(r);",
                registry=registry,
                mode=mode,
            )
            assert len(calls) == 1
            assert "selected" in output and "DROP" not in output and "WRONG" not in repr(request)
            result = ledger["mcp__fixture::read"]
            assert not result["is_error"]
            assert result["code_mode_output"]["value"] == {
                "content": [
                    {
                        "type": "resource",
                        "resource": {"uri": "x", "blob": "selected"},
                    }
                ]
            }
        finally:
            await client.aclose()

    asyncio.run(scenario())
