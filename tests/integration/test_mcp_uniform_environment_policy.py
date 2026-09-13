"""Every MCP declaration obeys host environment authority before connection."""

import asyncio

import pytest
from test_mcp_server_requirements import FinalModel
from test_mcp_tool_approval import Client

from corki.config import CorkiSettings, MCPServerSettings
from corki.config.mcp_requirements import MCPRequirements
from corki.core import LangGraphRuntime
from corki.mcp.catalog import MCPCatalog, MCPCatalogSource, MCPRegistration
from corki.mcp.runtime_environment import MCPRuntimeContext
from corki.protocol.events import TurnCompleted
from corki.tools import ToolRegistry


@pytest.mark.parametrize("kind", ["config", "compatibility", "extension", "hosted"])
@pytest.mark.parametrize("name", ["codex_apps", "ordinary"])
@pytest.mark.parametrize("allowed", [False, True])
def test_environment_authority_precedes_transport(tmp_path, monkeypatch, kind, name, allowed):
    async def scenario():
        clients = []

        class Context(MCPRuntimeContext):
            def requirements_for(self, environment_id):
                assert environment_id == "local"
                return MCPRequirements() if allowed else MCPRequirements(servers=())

        def factory(settings):
            client = Client(settings)
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        model = FinalModel()
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(tmp_path, skills_enabled=False, tool_search_mode="disabled"),
            model=model,
            registry=ToolRegistry(),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
            mcp_runtime_context=Context(),
            mcp_catalog=MCPCatalog(
                (
                    MCPRegistration(
                        MCPServerSettings(name, "http", url="https://fixture.invalid"),
                        MCPCatalogSource(
                            "extension" if kind == "hosted" else kind,
                            None if kind == "config" else "fixture",
                            host_owned_apps=kind == "hosted",
                        ),
                    ),
                )
            ),
        )
        try:
            events = [event async for event in runtime.stream("hello")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(clients) == int(allowed)
            if not allowed:
                assert not any(t.name.startswith("mcp__") for t in model.requests[0].tools)
                assert "environment requirements" in repr(runtime._mcp_manager.warnings)
        finally:
            await runtime.aclose()
        assert all(client.closed for client in clients)

    asyncio.run(scenario())
