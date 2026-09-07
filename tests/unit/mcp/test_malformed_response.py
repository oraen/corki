import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from corki.config import MCPServerSettings
from corki.mcp.client import (
    HttpMCPClient,
    MCPProtocolError,
    StdioMCPClient,
    _decode_http_response,
    validate_tool_result,
)
from corki.mcp.tools import MCPTool
from corki.protocol.ids import ToolCallId
from corki.protocol.tools import ToolCall
from corki.tools import ToolContext, ToolExecutor, ToolRegistry


@pytest.mark.parametrize("response_id", [True, 1.0, "1", None, 2])
def test_http_request_id_requires_exact_integer(response_id):
    async def scenario():
        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"id": response_id, "result": {}})
            ),
        )
        try:
            with pytest.raises(MCPProtocolError, match="id"):
                await client.request("tools/call", {})
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "invalid", [b'{"x":NaN}', b'{"x":1e999}', b'{"x":"\\ud800"}', b"\xff", b"{PRIVATE"]
)
def test_invalid_json_becomes_bounded_protocol_error(invalid):
    with pytest.raises(MCPProtocolError) as caught:
        _decode_http_response(httpx.Response(200, content=invalid))
    assert "PRIVATE" not in str(caught.value) and len(str(caught.value)) < 100


def test_sse_skips_invalid_json_and_noninteger_ids():
    body = "\n\n".join(
        "data: " + value
        for value in [
            '{"id":true,"result":{}}',
            '{"id":1.0,"result":{}}',
            '{"id":1,"result":NaN}',
            '{"id":1,"result":"\\ud800"}',
            '{"id":1,"result":{"ok":true}}',
        ]
    )
    response = httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)
    assert _decode_http_response(response, expected_id=1)["result"] == {"ok": True}


def test_stdio_skips_invalid_json_and_noninteger_ids():
    async def scenario():
        client = StdioMCPClient(MCPServerSettings("docs", "stdio", command="unused"))
        reader = asyncio.StreamReader()
        for packet in [
            {"id": True},
            {"id": 1.0},
            {"id": 1, "result": float("nan")},
            {"id": 1, "result": "ok"},
        ]:
            reader.feed_data(json.dumps(packet).encode() + b"\n")
        reader.feed_eof()
        client._process = SimpleNamespace(stdout=reader)
        future = asyncio.get_running_loop().create_future()
        client._pending[1] = future
        await client._read_loop()
        assert (await future)["result"] == "ok"

    asyncio.run(scenario())


@pytest.mark.parametrize("result", [None, {"content": "bad"}, {"isError": 1}])
def test_custom_client_malformed_result_is_not_dispatch_error(tmp_path, result):
    async def scenario():
        class Client:
            async def call_tool(self, name, arguments):
                return result

        registry = ToolRegistry()
        registry.register(MCPTool("docs", {"name": "read"}, Client()))
        value = await ToolExecutor(registry, output_char_budget=4000).execute(
            ToolCall(ToolCallId("bad"), "mcp__docs__read", {}), ToolContext(cwd=tmp_path)
        )
        assert value.is_error and not value.dispatch_error
        assert value.code_mode_output.value["isError"] is True

    asyncio.run(scenario())


def test_remote_cancellation_is_control_flow(tmp_path):
    async def scenario():
        class Client:
            async def call_tool(self, name, arguments):
                raise asyncio.CancelledError

        tool = MCPTool("docs", {"name": "read"}, Client())
        with pytest.raises(asyncio.CancelledError):
            await tool.execute(
                ToolCall(ToolCallId("cancel"), "mcp__docs__read", {}), ToolContext(cwd=tmp_path)
            )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "value", [{}, {"content": [], "isError": None}, {"content": [None, {"type": []}]}]
)
def test_existing_optional_fields_and_opaque_block_compatibility(value):
    assert validate_tool_result(value) is value
