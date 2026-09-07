import asyncio
import json

import httpx
import pytest

from corki.config import MCPServerSettings
from corki.mcp.client import PROTOCOL_VERSION, HttpMCPClient
from corki.mcp.manager import MCPManager
from corki.tools import ToolRegistry


@pytest.mark.parametrize("instructions", [None, "", "  搜索文档。\n  ", 17, True, {}, []])
def test_initialize_instructions_validation_isolates_invalid_server(monkeypatch, instructions):
    async def scenario():
        clients, requests = {}, []
        valid = instructions is None or isinstance(instructions, str)

        def factory(settings):
            def handler(request):
                message = json.loads(request.content)
                method = message["method"]
                requests.append((settings.name, method))
                if method == "notifications/initialized":
                    return httpx.Response(202)
                if method == "initialize":
                    result = {"protocolVersion": PROTOCOL_VERSION}
                    if settings.name == "test":
                        result["instructions"] = instructions
                elif method == "tools/list":
                    result = {"tools": [{"name": "lookup"}]}
                else:
                    raise AssertionError(method)
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": message["id"], "result": result}
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(handler))
            clients[settings.name] = client
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        registry = ToolRegistry()
        manager = MCPManager(
            tuple(
                MCPServerSettings(name, "http", url=f"https://{name}.test/mcp")
                for name in ("test", "healthy")
            ),
            registry,
            defer_tools=True,
        )
        try:
            await manager.start()
            assert registry.spec("mcp__healthy__lookup").source_description is None
            if valid:
                assert clients["test"].server_instructions == instructions
                assert registry.spec("mcp__test__lookup").source_description == (
                    (instructions or "").strip() or None
                )
                assert not manager.warnings
            else:
                assert registry.get("mcp__test__lookup") is None
                assert not clients["test"]._initialized
                assert clients["test"].server_instructions is None
                assert clients["test"]._client.is_closed
                assert [method for name, method in requests if name == "test"] == ["initialize"]
                assert len(manager.warnings) == 1
                assert "initialize instructions must be a string" in manager.warnings[0]
        finally:
            await manager.aclose()
        assert all(client._client.is_closed for client in clients.values())

    asyncio.run(scenario())
