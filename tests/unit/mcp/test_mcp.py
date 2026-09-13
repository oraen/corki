import asyncio
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from corki.config import MCPServerSettings
from corki.mcp.client import PROTOCOL_VERSION, HttpMCPClient, MCPClient, _decode_http_response
from corki.mcp.manager import MCPManager
from corki.mcp.tools import MCPTool
from corki.protocol.ids import ToolCallId
from corki.protocol.tools import ToolCall
from corki.tools import ToolContext, ToolExecutor, ToolRegistry


class FakeMCPClient(MCPClient):
    def __init__(self, settings: MCPServerSettings) -> None:
        super().__init__(settings)
        self.closed = False

    async def start(self) -> None:
        return None

    async def list_tools(self) -> tuple[dict[str, Any], ...]:
        return (
            {
                "name": "search",
                "description": "Search documents",
                "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
                "annotations": {"readOnlyHint": True},
            },
        )

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "content": [{"type": "text", "text": f"{name}:{arguments['q']}"}],
            "structuredContent": {"count": 1},
        }

    async def list_resources(self, cursor=None):
        return {"resources": [{"uri": "memory://guide", "name": "guide"}]}

    async def read_resource(self, uri: str) -> Mapping[str, Any]:
        return {"contents": [{"uri": uri, "text": "resource body"}]}

    async def list_resource_templates(self, cursor=None):
        return {"resourceTemplates": [{"uriTemplate": "memory://{name}", "name": "memory"}]}

    async def list_prompts(self) -> tuple[dict[str, Any], ...]:
        return ({"name": "review"},)

    async def get_prompt(self, name: str, arguments: Mapping[str, str]) -> Mapping[str, Any]:
        return {"description": name, "messages": [], "arguments": dict(arguments)}

    async def _exchange(self, message: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError(message)

    async def _send_notification(self, message: dict[str, Any]) -> None:
        raise AssertionError(message)

    async def aclose(self) -> None:
        self.closed = True


def test_manager_registers_and_executes_namespaced_mcp_tools(tmp_path: Path, monkeypatch) -> None:
    settings = MCPServerSettings("docs", "http", url="https://example.test/mcp")
    client = FakeMCPClient(settings)
    monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: client)
    registry = ToolRegistry()
    manager = MCPManager((settings,), registry)

    async def scenario() -> str:
        await manager.start()
        executor = ToolExecutor(registry, output_char_budget=1_000)
        result = await executor.execute(
            ToolCall(ToolCallId("mcp-1"), "mcp__docs::search", {"q": "langgraph"}),
            ToolContext(cwd=tmp_path),
        )
        await manager.aclose()
        return result.content

    content = asyncio.run(scenario())

    assert content.startswith("Wall time: ")
    assert content.split("\nOutput:\n", 1)[1] == '{"count":1}'
    assert "search:langgraph" not in content
    assert "mcp__docs::search" in manager.tool_names
    assert "read_mcp_resource" in manager.tool_names
    assert client.closed


def test_cancelled_manager_start_rolls_back_clients_and_registry(
    monkeypatch,
) -> None:
    class BlockingClient(FakeMCPClient):
        def __init__(self, settings: MCPServerSettings) -> None:
            super().__init__(settings)
            self.started = asyncio.Event()

        async def start(self) -> None:
            self.started.set()
            await asyncio.Event().wait()

    first_settings = MCPServerSettings("first", "http", url="https://first.test/mcp")
    second_settings = MCPServerSettings("second", "http", url="https://second.test/mcp")
    first = FakeMCPClient(first_settings)
    second = BlockingClient(second_settings)
    clients = {"first": first, "second": second}
    monkeypatch.setattr("corki.mcp.manager.create_client", lambda settings: clients[settings.name])
    registry = ToolRegistry()
    manager = MCPManager((first_settings, second_settings), registry)

    async def scenario() -> tuple[FakeMCPClient, FakeMCPClient]:
        startup = asyncio.create_task(manager.start())
        await second.started.wait()
        startup.cancel()
        with pytest.raises(asyncio.CancelledError):
            await startup
        assert registry.specs() == ()
        assert manager.tool_names == ()
        retry_first = FakeMCPClient(first_settings)
        retry_second = FakeMCPClient(second_settings)
        clients.update(first=retry_first, second=retry_second)
        await manager.start()
        await manager.aclose()
        return retry_first, retry_second

    retry_first, retry_second = asyncio.run(scenario())

    assert first.closed
    assert second.closed
    assert {"mcp__first::search", "mcp__second::search"}.issubset(manager.tool_names)
    assert retry_first.closed
    assert retry_second.closed


def test_aggregate_mcp_resource_tools_preserve_server_provenance(
    tmp_path: Path, monkeypatch
) -> None:
    settings = MCPServerSettings("docs", "http", url="https://example.test/mcp")
    monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: FakeMCPClient(settings))
    registry = ToolRegistry()
    manager = MCPManager((settings,), registry)

    async def scenario() -> tuple[str, str]:
        await manager.start()
        executor = ToolExecutor(registry, output_char_budget=2_000)
        listed = await executor.execute(
            ToolCall(ToolCallId("resources-1"), "list_mcp_resources", {}),
            ToolContext(cwd=tmp_path),
        )
        read = await executor.execute(
            ToolCall(
                ToolCallId("resources-2"),
                "read_mcp_resource",
                {"server": "docs", "uri": "memory://guide"},
            ),
            ToolContext(cwd=tmp_path),
        )
        await manager.aclose()
        return listed.content, read.content

    listed, read = asyncio.run(scenario())

    assert json.loads(listed) == {
        "resources": [{"server": "docs", "uri": "memory://guide", "name": "guide"}]
    }
    assert json.loads(read)["contents"][0]["text"] == "resource body"


def test_streamable_http_sse_response_is_decoded() -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text='event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{}}\n\n',
    )

    assert _decode_http_response(response)["id"] == 1


def test_streamable_http_skips_notifications_before_matching_response() -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text=(
            'data: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
            'data: {"jsonrpc":"2.0","id":7,"result":{"ok":true}}\n\n'
        ),
    )

    assert _decode_http_response(response, expected_id=7)["result"] == {"ok": True}


def test_http_uses_server_negotiated_protocol_version_after_initialize() -> None:
    observed_notification_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(204)
        message = json.loads(request.content)
        if "id" in message:
            assert message["params"]["protocolVersion"] == PROTOCOL_VERSION
            return httpx.Response(
                200,
                headers={"mcp-session-id": "session-1"},
                json={
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "serverInfo": {"name": "legacy", "version": "1"},
                    },
                },
            )
        observed_notification_headers.update(request.headers)
        return httpx.Response(202)

    async def scenario() -> None:
        settings = MCPServerSettings("legacy", "http", url="https://example.test/mcp")
        client = HttpMCPClient(settings)
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        await client.start()
        await client.aclose()

    asyncio.run(scenario())

    assert observed_notification_headers["mcp-protocol-version"] == "2025-03-26"
    assert observed_notification_headers["mcp-session-id"] == "session-1"


def test_stdio_transport_negotiates_lists_and_calls_tool(tmp_path: Path) -> None:
    server = """
import json, sys
for line in sys.stdin:
    message = json.loads(line)
    if 'id' not in message:
        continue
    method = message['method']
    if method == 'initialize':
        assert message['params']['protocolVersion'] == '2025-06-18'
        result = {'protocolVersion': '2025-03-26', 'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'fixture', 'version': '1'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'echo', 'description': 'echo',
                             'inputSchema': {'type': 'object'}}]}
    elif method == 'tools/call':
        result = {'content': [{'type': 'text', 'text': message['params']['arguments']['text']}]}
    print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}), flush=True)
"""
    settings = MCPServerSettings(
        "fixture",
        "stdio",
        command=sys.executable,
        args=("-u", "-c", server),
        cwd=tmp_path,
        timeout_seconds=3,
    )
    registry = ToolRegistry()
    manager = MCPManager((settings,), registry)

    async def scenario() -> str:
        await manager.start()
        result = await ToolExecutor(registry, output_char_budget=1_000).execute(
            ToolCall(ToolCallId("stdio-1"), "mcp__fixture::echo", {"text": "hello"}),
            ToolContext(cwd=tmp_path),
        )
        await manager.aclose()
        return result.content

    assert asyncio.run(scenario()).split("\nOutput:\n", 1)[1] == "hello"
    assert manager.warnings == ()
    assert PROTOCOL_VERSION == "2025-06-18"


def test_mcp_image_content_becomes_a_model_attachment(tmp_path: Path) -> None:
    class ImageClient(FakeMCPClient):
        async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
            return {"content": [{"type": "image", "mimeType": "image/png", "data": "aGVsbG8="}]}

    settings = MCPServerSettings("vision", "http", url="https://example.test/mcp")
    tool = MCPTool(
        "vision", {"name": "capture", "inputSchema": {"type": "object"}}, ImageClient(settings)
    )

    result = asyncio.run(
        tool.execute(
            ToolCall(ToolCallId("image-1"), "mcp__vision::capture", {}),
            ToolContext(cwd=tmp_path),
        )
    )

    assert result.content_items[0].text.startswith("Wall time: ")
    assert result.content_items[1].data_url == "data:image/png;base64,aGVsbG8="
    assert not result.attachments
    assert "base64" not in result.display_content
