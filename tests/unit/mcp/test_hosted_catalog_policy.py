"""Hosted source identity never follows a name, remote hint, or unrelated task."""

import asyncio
from dataclasses import replace

import pytest

from corki.config import MCPServerSettings
from corki.config.mcp_requirements import MCPRequirements
from corki.mcp.catalog import MCPCatalog, MCPCatalogSource, MCPRegistration
from corki.mcp.catalog_policy import catalog_limit, effective_catalog_limit
from corki.mcp.client import MCPProtocolError
from corki.mcp.connection import MCPConnection
from corki.mcp.tool_catalog_cache import MCPToolCatalogCache


@pytest.mark.parametrize("kind", ["config", "extension", "hosted", "compatibility"])
@pytest.mark.parametrize("name", ["codex_apps", "ordinary"])
@pytest.mark.parametrize("environment", ["local", "remote"])
def test_source_identity_and_environment_policy(kind, name, environment):
    settings = MCPServerSettings(
        name, "http", url="https://fixture.invalid", environment_id=environment
    )
    source = MCPCatalogSource(
        "extension" if kind == "hosted" else kind,
        None if kind == "config" else "fixture",
        host_owned_apps=kind == "hosted",
    )
    expected = (
        name == "codex_apps" and environment == "local" and kind in {"hosted", "compatibility"}
    )
    assert source.is_host_owned_apps(settings) is expected
    catalog = MCPCatalog((MCPRegistration(settings, source),))
    denied = catalog.constrain_environments(lambda _: MCPRequirements(servers=()))
    assert not denied.servers[0].settings.enabled
    # Existing controller/config vetoes cannot be lifted by attachment exemption.
    disabled = MCPCatalog((MCPRegistration(replace(settings, enabled=False), source),))
    assert (
        not disabled.constrain_environments(lambda _: MCPRequirements()).servers[0].settings.enabled
    )
    assert catalog.materialize((settings,)).servers[0].source == source
    overlay = MCPCatalog((MCPRegistration(settings), MCPRegistration(settings, source)))
    assert not overlay.constrain(MCPRequirements(servers=())).servers[0].settings.enabled


@pytest.mark.parametrize(
    "kind,value",
    [
        ("config", True),
        ("compatibility", True),
        ("plugin", True),
        ("extension", 1),
        ("extension", "true"),
    ],
)
def test_explicit_hosted_flag_is_typed_extension_only(kind, value):
    with pytest.raises(ValueError, match="host-owned Apps"):
        MCPCatalogSource(kind, None if kind == "config" else "fixture", host_owned_apps=value)


def test_catalog_capacity_is_exact_client_task_local_and_restored():
    async def scenario():
        first, second = object(), object()
        entered, release = asyncio.Event(), asyncio.Event()

        async def high():
            with catalog_limit(first, 2048):
                assert effective_catalog_limit(first) == 2048
                assert effective_catalog_limit(second) == 2048
                entered.set()
                await release.wait()
                with catalog_limit(first, 2048):
                    assert effective_catalog_limit(first) == 2048
                assert effective_catalog_limit(first) == 2048
            assert effective_catalog_limit(first) == 2048

        task = asyncio.create_task(high())
        await entered.wait()
        assert effective_catalog_limit(first) == 2048
        release.set()
        await task

    asyncio.run(scenario())


def test_custom_client_cannot_publish_or_reuse_oversized_catalog():
    async def scenario():
        class Client:
            settings = MCPServerSettings("ordinary", "http", url="https://fixture.invalid")
            server_instructions = None
            is_closed = False

            async def start(self):
                pass

            async def list_tools(self):
                return tuple(
                    {"name": f"tool_{n}", "inputSchema": {"type": "object"}} for n in range(2049)
                )

            async def aclose(self):
                self.is_closed = True

        client = Client()
        entry = MCPToolCatalogCache().context(client.settings)
        connection = MCPConnection(client, lambda _: None, cache_context=entry)
        try:
            with pytest.raises(MCPProtocolError, match="2048"):
                await connection.start()
            assert not connection.can_reuse(client.settings)
            with pytest.raises(RuntimeError, match="not completed"):
                _ = connection.definitions
            assert entry.current_tools_or() is None
        finally:
            await connection.aclose()
        assert client.is_closed

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [True, 1, 0, -1, 8192, 8193, "2048", None])
def test_catalog_capacity_rejects_non_host_limits(value):
    with pytest.raises(ValueError, match="host-selected"), catalog_limit(object(), value):
        pass


def test_legacy_apps_name_uses_ordinary_definition_cache():
    cache = MCPToolCatalogCache()
    apps = MCPServerSettings("codex_apps", "http", url="https://fixture.invalid")
    assert cache.context(apps) is not None
    assert cache.context(replace(apps, name="ordinary")) is not None


def test_pending_and_ready_reuse_require_the_same_catalog_capacity():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class Client:
            settings = MCPServerSettings("codex_apps", "http", url="https://fixture.invalid")
            server_instructions = None
            is_closed = False

            async def start(self):
                pass

            async def list_tools(self):
                entered.set()
                await release.wait()
                assert effective_catalog_limit(self) == 2048
                return ({"name": "needle", "inputSchema": {"type": "object"}},)

            async def aclose(self):
                self.is_closed = True

        client = Client()
        connection = MCPConnection(client, lambda _: None, catalog_item_limit=2048)
        task = asyncio.create_task(connection.start())
        try:
            await entered.wait()
            assert connection.can_reuse_pending(client.settings, catalog_item_limit=2048)
            assert not connection.can_reuse_pending(client.settings, catalog_item_limit=8192)
            release.set()
            await task
            assert connection.can_reuse(client.settings, catalog_item_limit=2048)
            assert not connection.can_reuse(client.settings, catalog_item_limit=8192)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await connection.aclose()
        assert client.is_closed

    asyncio.run(scenario())
