"""Required MCP startup is session admission, not a recoverable tool Observation."""

import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import PROTOCOL_VERSION, HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.items import UserMessageItem
from corki.tools import ToolRegistry


@pytest.mark.parametrize("value", ["true", 1, None, [], {}])
def test_required_configuration_rejects_non_boolean(value):
    with pytest.raises(ValueError, match="required.*boolean"):
        MCPServerSettings.from_mapping(
            "server", {"url": "https://server.test/mcp", "required": value}
        )


@pytest.mark.parametrize("required", [False, True])
def test_required_configuration_is_preserved(required):
    settings = MCPServerSettings.from_mapping(
        "server", {"url": "https://server.test/mcp", "required": required}
    )
    assert settings.required is required


def test_required_defaults_to_false():
    assert not MCPServerSettings("server", "http", url="https://server.test/mcp").required
    assert not MCPServerSettings.from_mapping("server", {"url": "https://server.test/mcp"}).required


@pytest.mark.parametrize("policy", ["optional", "required", "disabled"])
def test_required_mcp_admission_failure_retry_and_owned_cleanup(tmp_path, monkeypatch, policy):
    async def scenario():
        failing = {"a_missing", "z_missing"}
        contacted, closed, clients, requests, events = [], [], [], [], []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        def client_factory(settings):
            async def handle(request):
                if request.method == "GET":
                    return httpx.Response(405)
                if request.method == "DELETE":
                    closed.append(settings.name)
                    return httpx.Response(204)
                message = json.loads(request.content)
                method = message["method"]
                contacted.append((settings.name, method))
                if method == "notifications/initialized":
                    return httpx.Response(202)
                if method == "initialize":
                    result = {
                        "serverInfo": {"name": "fixture", "version": "1"},
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                    }
                elif method == "tools/list":
                    if settings.name in failing:
                        return httpx.Response(
                            200,
                            json={
                                "jsonrpc": "2.0",
                                "id": message["id"],
                                "error": {"code": -32000, "message": "startup unavailable"},
                            },
                        )
                    result = {"tools": [{"name": "lookup", "inputSchema": {"type": "object"}}]}
                else:
                    raise AssertionError(method)
                return httpx.Response(
                    200,
                    headers={"mcp-session-id": settings.name},
                    json={"jsonrpc": "2.0", "id": message["id"], "result": result},
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(handle))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", client_factory)
        settings = tuple(
            MCPServerSettings.from_mapping(
                name,
                {
                    "url": f"https://{name}.test/mcp",
                    "required": policy != "optional",
                    "enabled": policy != "disabled" or name == "healthy",
                },
            )
            for name in ("z_missing", "healthy", "a_missing")
        )
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                mcp_servers=settings,
                execution_permissions=None,
            ),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=Model(),
        )
        try:
            if policy == "required":
                with pytest.raises(RuntimeError, match="required MCP servers failed") as error:
                    async for event in runtime.stream("not admitted"):
                        events.append(event)
                assert "a_missing" in str(error.value) and "z_missing" in str(error.value)
                assert str(error.value).index("a_missing") < str(error.value).index("z_missing")
                assert not events and not requests
                assert runtime._compiled is None and runtime._active_run is None
                assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
                assert not await runtime._repository.load_items(runtime.thread_id)
                # Healthy published siblings remain reusable, but failed
                # servers cannot contribute callable definitions.
                published_names = tuple(spec.name for spec in runtime._registry.specs())
                assert any("healthy" in name for name in published_names)
                assert not any(
                    failed_name in name for name in published_names for failed_name in failing
                )
                assert "healthy" not in closed
                # A second failed attempt cannot bypass validation via _started.
                with pytest.raises(RuntimeError, match="required MCP servers failed"):
                    await anext(runtime.stream("still not admitted"))
                assert not requests
                assert tuple(spec.name for spec in runtime._registry.specs()) == published_names
                # Retry only startup; no model or side-effecting tools/call was admitted.
                failing.clear()
            result = [event async for event in runtime.stream("accepted")]
            assert isinstance(result[-1], TurnCompleted)
            assert len(requests) == 1
            assert [i.content for i in requests[0].items if isinstance(i, UserMessageItem)] == [
                "accepted"
            ]
            assert any("healthy" in name for name in runtime._mcp_manager.tool_names)
            if policy == "optional":
                assert any("startup unavailable" in w for w in runtime._mcp_manager.warnings)
            if policy == "disabled":
                assert {name for name, _ in contacted} == {"healthy"}
            if policy == "required":
                assert contacted.count(("healthy", "initialize")) == 1
                assert contacted.count(("a_missing", "initialize")) == 3
        finally:
            await runtime.aclose()
        assert clients and all(client._client.is_closed for client in clients)
        assert "healthy" in closed

    asyncio.run(scenario())
