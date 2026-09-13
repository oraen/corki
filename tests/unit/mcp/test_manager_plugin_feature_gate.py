"""Manager package gates cannot be bypassed by cache, refresh or named calls."""

import asyncio
from dataclasses import replace

import pytest
from test_reconciliation import Client, setting

from corki.config.mcp_requirements import MCPServerSource
from corki.mcp.catalog import MCPCatalog, MCPCatalogSource, MCPRegistration
from corki.mcp.manager import MCPManager
from corki.mcp.tool_catalog_cache import MCPToolCatalogCache
from corki.tools import ToolRegistry


@pytest.mark.parametrize("kind", ["legacy", "plugin", "selected_plugin"])
@pytest.mark.parametrize("cached", [False, True])
def test_disabled_sources_stay_absent_at_all_manager_entrypoints(monkeypatch, kind, cached):
    async def scenario():
        clients = []

        def factory(settings):
            client = Client(settings)
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        server = setting(required=True)
        cache = MCPToolCatalogCache()
        options = {
            "server_sources": {"docs": MCPServerSource("docs", "fixture")},
            "tool_catalog_cache": cache,
            "lazy_when_cached": cached,
        }
        if kind != "legacy":
            options["catalog"] = MCPCatalog(
                (MCPRegistration(server, MCPCatalogSource(kind, "fixture")),)
            )
        if cached:
            warm = MCPManager((server,), ToolRegistry(), **options)
            try:
                await warm.start()
                assert clients and clients[0].lists == 1
            finally:
                await warm.aclose()
            assert all(client.is_closed for client in clients)
            clients.clear()
        registry = ToolRegistry()
        manager = MCPManager((server,), registry, plugins_enabled=False, **options)
        try:
            if kind != "legacy":
                assert manager.catalog.servers == ()
            await manager.start()
            for update in (manager.request_reconcile, manager.request_refresh):
                update((replace(server, url="https://changed.test"),))
                await manager.start()
                assert not clients
                assert not manager.tool_names
                assert await manager.list_capability("resources", None) == {"resources": []}
                with pytest.raises(KeyError):
                    await manager.call_tool("docs", "read", {})
                with pytest.raises(KeyError):
                    await manager.read_resource("docs", "fixture://resource")
                with pytest.raises(KeyError):
                    await manager.get_prompt("docs", "prompt", {})
                assert not clients, "named calls must not initialize filtered sources"
            # A new typed host declaration is distinct from re-materializing the
            # existing plugin. The stale legacy identity cannot ban the host source.
            host = MCPRegistration(server, MCPCatalogSource("extension", "host"))
            manager.request_catalog(MCPCatalog((host,)))
            await manager.start()
            assert manager.catalog.servers == (host,)
            assert "read" in repr(await manager.call_tool("docs", "read", {}))
            assert len(clients) == 1 and clients[0].calls
        finally:
            await manager.aclose()
        assert all(client.is_closed for client in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("identity", [None, MCPServerSource("raw-config")])
def test_plugin_looking_names_are_not_source_authority(monkeypatch, identity):
    async def scenario():
        clients = []

        def factory(settings):
            client = Client(settings)
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        server = setting(name="plugin__fixture__docs")
        manager = MCPManager(
            (server,),
            ToolRegistry(),
            plugins_enabled=False,
            server_sources={} if identity is None else {server.name: identity},
        )
        try:
            await manager.start()
            assert "read" in repr(await manager.call_tool(server.name, "read", {}))
            assert len(clients) == 1
        finally:
            await manager.aclose()
        assert clients[0].is_closed

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [None, 0, 1, "false", []])
def test_plugin_feature_requires_a_boolean(value):
    with pytest.raises(ValueError, match="plugin enablement"):
        MCPManager((), ToolRegistry(), plugins_enabled=value)
