"""Real Runtime resolves source declarations before constructing any transport."""

import asyncio
import json
from dataclasses import replace

import pytest
from test_mcp_server_requirements import make_runtime
from test_mcp_tool_approval import Model

from corki.config import CorkiSettings, MCPServerSettings
from corki.config.features import parse_mcp_servers
from corki.core import LangGraphRuntime
from corki.mcp.catalog import MCPCatalog, MCPCatalogSource, MCPRegistration, MCPRemoval
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ToolCallItem
from corki.tools import ToolRegistry


def package(tmp_path, name):
    root = tmp_path / name
    manifest = root / ".codex-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "name": name,
                "mcpServers": {"docs": {"url": f"https://{name}.test/mcp"}},
            }
        )
    )
    return root


@pytest.mark.parametrize("mode", ["direct", "native", "compatible", "code_mode"])
@pytest.mark.parametrize("configured", [True, False])
def test_only_winning_raw_server_is_exposed_and_executed(tmp_path, monkeypatch, mode, configured):
    alpha, beta = package(tmp_path, "alpha"), package(tmp_path, "beta")
    servers = parse_mcp_servers({"docs": {"url": "https://config.test/mcp"}}) if configured else ()

    async def scenario():
        runtime, clients, model = make_runtime(
            tmp_path,
            monkeypatch,
            requirements=None,
            servers=servers,
            plugins=(beta, alpha),
            mode=mode,
            model=Model(mode),
        )
        try:
            events = [event async for event in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(clients) == 1
            assert clients[0].settings.url == (
                "https://config.test/mcp" if configured else "https://alpha.test/mcp"
            )
            assert clients[0].calls == [("write", {"value": 1})]
            assert "remote effect" in repr(model.requests[-1].items)
        finally:
            await runtime.aclose()
        assert clients[0].closed

    asyncio.run(scenario())


def test_disabled_config_winner_prevents_plugin_fallback(tmp_path, monkeypatch):
    alpha = package(tmp_path, "alpha")
    servers = parse_mcp_servers(
        {
            "docs": {"url": "https://config.test/mcp", "enabled": False},
        }
    )

    async def scenario():
        runtime, clients, model = make_runtime(
            tmp_path,
            monkeypatch,
            requirements=None,
            servers=servers,
            plugins=(alpha,),
        )
        try:
            events = [event async for event in runtime.stream("hello")]
            assert isinstance(events[-1], TurnCompleted)
            assert clients == []
            assert not any(tool.name.startswith("mcp__") for tool in model.requests[0].tools)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "native", "compatible", "code_mode"])
def test_source_change_rechecks_policy_even_with_identical_transport(tmp_path, monkeypatch, mode):
    alpha = package(tmp_path, "alpha")

    async def scenario():
        class ChangingModel(Model):
            async def stream(self, request):
                async for event in super().stream(request):
                    if any(
                        isinstance(item, ToolCallItem) and item.call.name != "tool_search"
                        for item in event.items
                    ):
                        old = runtime.mcp_catalog.servers[0]
                        runtime.request_mcp_catalog(MCPCatalog((MCPRegistration(old.settings),)))
                    yield event

        runtime, clients, model = make_runtime(
            tmp_path,
            monkeypatch,
            plugins=(alpha,),
            mode=mode,
            model=ChangingModel(mode),
            requirements={"mcp_servers": {"docs": {"identity": {"url": "https://config.test"}}}},
        )
        try:
            events = [event async for event in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(clients) == 1 and clients[0].calls == []
            assert "managed requirements" in repr(model.requests[-1].items)
            assert runtime.mcp_catalog.servers[0].source.kind == "config"
        finally:
            await runtime.aclose()
        assert clients[0].closed

    asyncio.run(scenario())


@pytest.mark.parametrize("selected", [False, True])
def test_host_overlay_retains_disabled_veto_except_selected_plugin(tmp_path, monkeypatch, selected):
    async def scenario():
        runtime, clients, _ = make_runtime(tmp_path, monkeypatch, requirements=None)
        source = MCPCatalogSource("selected_plugin", "selected") if selected else MCPCatalogSource()
        disabled = MCPRegistration(
            MCPServerSettings("docs", "http", url="https://base.test", enabled=False),
            source,
        )
        overlay = MCPRegistration(
            replace(disabled.settings, url="https://host.test", enabled=True),
            MCPCatalogSource("extension", "host"),
        )
        runtime.request_mcp_catalog(MCPCatalog((disabled,)).extend(overlay))
        try:
            events = [event async for event in runtime.stream("hello")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(clients) == int(selected)
            if selected:
                await runtime._mcp_manager.call_tool("docs", "write", {"value": 1})
                assert clients[0].calls == [("write", {"value": 1})]
            assert runtime.mcp_catalog.servers[0].source == overlay.source
            assert runtime.mcp_catalog.servers[0].settings.enabled is selected
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_admitted_call_survives_source_removal_but_new_calls_do_not(tmp_path, monkeypatch):
    async def scenario():
        original = MCPServerSettings(
            "docs", "http", url="https://allowed.test", default_tools_approval_mode="prompt"
        )
        runtime, clients, model = make_runtime(
            tmp_path,
            monkeypatch,
            requirements=None,
            servers=(original,),
            model=Model(),
            approval="on-request",
        )
        entered, release = asyncio.Event(), asyncio.Event()

        async def host(request):
            entered.set()
            await release.wait()
            runtime.respond_mcp_elicitation(request.server_name, request.request_id, "accept")

        async def consume():
            return [event async for event in runtime.stream("needle")]

        runtime.set_mcp_elicitation_handler(host)
        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            runtime.request_mcp_catalog(
                runtime.mcp_catalog.extend(
                    MCPRemoval("docs", MCPCatalogSource("extension", "host")),
                )
            )
            await runtime._mcp_manager.refresh_if_dirty()
            assert clients[0].calls == [] and not clients[0].closed
            with pytest.raises(KeyError, match="unknown MCP server"):
                await runtime._mcp_manager.call_tool("docs", "write", {})
            release.set()
            events = await asyncio.wait_for(task, 5)
            assert isinstance(events[-1], TurnCompleted)
            assert clients[0].calls == [("write", {"value": 1})]
            assert "remote effect" in repr(model.requests[-1].items)
            assert runtime.mcp_catalog.servers == ()
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()
        assert clients[0].closed

    asyncio.run(scenario())


def test_old_package_prefixed_route_cannot_alias_new_winner(tmp_path, monkeypatch):
    alpha = package(tmp_path, "alpha")

    async def scenario():
        runtime, clients, _ = make_runtime(
            tmp_path, monkeypatch, requirements=None, plugins=(alpha,)
        )
        try:
            _ = [event async for event in runtime.stream("hello")]
            with pytest.raises(KeyError, match="unknown MCP server"):
                await runtime._mcp_manager.call_tool("alpha__docs", "write", {})
            assert clients[0].calls == []
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_initial_host_catalog_is_captured_with_runtime_cwd(tmp_path, monkeypatch):
    from test_mcp_tool_approval import Client

    clients = []

    def factory(settings):
        client = Client(settings)
        clients.append(client)
        return client

    monkeypatch.setattr("corki.mcp.manager.create_client", factory)

    async def scenario():
        server = MCPServerSettings("docs", "http", url="https://host.test")
        action = MCPRegistration(server, MCPCatalogSource("extension", "host"))
        original = MCPCatalog((action,))
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, api_mode="responses"
            ),
            model=Model("compatible"),
            registry=ToolRegistry(),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
            mcp_catalog=original,
            mcp_requirements={"mcp_servers": {}},
        )
        # Replacing the caller's private catalog storage cannot affect the captured input.
        original._actions = ()
        try:
            events = [event async for event in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(clients) == 1 and clients[0].calls == [("write", {"value": 1})]
            assert clients[0].settings.cwd == tmp_path
            assert runtime.mcp_catalog.servers[0].source == action.source
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
