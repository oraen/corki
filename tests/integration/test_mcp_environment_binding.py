"""Unresolved environment declarations cannot execute against local resources."""

import asyncio
import json
from dataclasses import replace

import pytest
from test_mcp_server_requirements import make_runtime
from test_mcp_tool_approval import Model

from corki.config import CorkiSettings, managed_mcp
from corki.config.features import MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient, MCPProtocolError, StdioMCPClient, create_client
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ToolCallItem, ToolResultItem
from corki.tools import ToolRegistry


def server(environment="local", transport="http", name="docs"):
    fields = {"url": "https://fixture.invalid"} if transport == "http" else {"command": "never-run"}
    return MCPServerSettings.from_mapping(name, {**fields, "environment_id": environment})


@pytest.mark.parametrize("transport", ["stdio", "http"])
@pytest.mark.parametrize("entry", ["factory", "constructor"])
def test_low_level_local_clients_reject_unknown_environment_before_allocation(
    monkeypatch, transport, entry
):
    constructed = []

    def forbidden(*args, **kwargs):
        constructed.append(True)
        raise AssertionError("unknown environment must not allocate a local HTTP client")

    monkeypatch.setattr("corki.mcp.client.MCPHttpSession", forbidden)
    settings = server("missing-remote", transport)
    constructor = (
        create_client
        if entry == "factory"
        else (StdioMCPClient if transport == "stdio" else HttpMCPClient)
    )
    with pytest.raises(MCPProtocolError, match="unknown environment id"):
        constructor(settings)
    assert constructed == []


@pytest.mark.parametrize("transport", ["stdio", "http"])
@pytest.mark.parametrize("environment", ["missing-remote", "", "local "])
def test_unknown_environment_isolated_before_factory_and_healthy_server_remains(
    tmp_path, monkeypatch, transport, environment
):
    async def scenario():
        runtime, clients, model = await make_runtime(
            tmp_path,
            monkeypatch,
            requirements={},
            servers=(server(environment, transport), server(name="healthy")),
        )
        try:
            events = [event async for event in runtime.stream("hello")]
            assert isinstance(events[-1], TurnCompleted)
            assert [client.settings.name for client in clients] == ["healthy"]
            assert "unknown environment id" in repr(runtime._mcp_manager.warnings)
            assert not any(tool.name.startswith("mcp__docs") for tool in model.requests[0].tools)
            assert runtime.mcp_catalog.servers[0].settings.environment_id == environment
        finally:
            await runtime.aclose()
        assert all(client.closed for client in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "native", "compatible", "code_mode"])
@pytest.mark.parametrize("force", [False, True])
def test_environment_replacement_never_reuses_old_local_connection_or_replays_call(
    tmp_path, monkeypatch, mode, force
):
    async def scenario():
        class ChangeEnvironment(Model):
            async def stream(self, request):
                async for event in super().stream(request):
                    if any(
                        isinstance(item, ToolCallItem) and item.call.name != "tool_search"
                        for item in event.items
                    ):
                        update = (
                            runtime.request_mcp_refresh if force else runtime.request_mcp_reconcile
                        )
                        update((server("missing-remote"),))
                    yield event

        runtime, clients, model = await make_runtime(
            tmp_path,
            monkeypatch,
            requirements={},
            servers=(server(),),
            model=ChangeEnvironment(mode),
            mode=mode,
        )
        try:
            events = [event async for event in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(clients) == 1 and clients[0].calls == []
            assert "unknown environment id" in repr(runtime._mcp_manager.warnings)
            results = [
                item
                for item in model.requests[-1].items
                if isinstance(item, ToolResultItem) and item.tool_name != "tool_search"
            ]
            assert results and all(item.is_error is (mode != "code_mode") for item in results)
            if mode == "code_mode":
                assert all('"isError":true' in item.content.replace(" ", "") for item in results)
            assert all("unknown environment id" in item.content for item in results)
            assert runtime.mcp_catalog.servers[0].settings.environment_id == "missing-remote"
        finally:
            await runtime.aclose()
        assert clients[0].closed

    asyncio.run(scenario())


def test_unknown_environment_config_winner_does_not_fall_back_to_local_plugin(
    tmp_path, monkeypatch
):
    plugin = tmp_path / "package"
    manifest = plugin / ".codex-plugin/plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"name": "package", "mcpServers": {"docs": {"url": "https://plugin.invalid"}}}),
        encoding="utf-8",
    )

    async def scenario():
        runtime, clients, model = await make_runtime(
            tmp_path,
            monkeypatch,
            requirements={},
            servers=(server("missing-remote"),),
            plugins=(plugin,),
        )
        try:
            events = [event async for event in runtime.stream("hello")]
            assert isinstance(events[-1], TurnCompleted)
            assert clients == []
            assert runtime.mcp_catalog.servers[0].source.kind == "config"
            assert "unknown environment id" in repr(runtime._mcp_manager.warnings)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["native", "compatible"])
@pytest.mark.parametrize("restriction", ["environment", "managed_file"])
def test_cold_history_discovery_cannot_restore_old_authority(
    tmp_path, monkeypatch, mode, restriction
):
    path = tmp_path / "requirements.toml"
    path.write_text('[mcp_servers.docs.identity]\nurl="https://fixture.invalid"', encoding="utf-8")
    monkeypatch.setattr(managed_mcp, "system_requirements_path", lambda: path)

    async def scenario():
        first, clients, model = await make_runtime(
            tmp_path,
            monkeypatch,
            requirements=None,
            servers=(server(),),
            model=Model(mode),
            mode=mode,
        )
        try:
            events = [event async for event in first.stream("needle")]
            assert isinstance(events[-1], TurnCompleted)
            thread = first.thread_id
            assert clients[0].calls == [("write", {"value": 1})]
        finally:
            await first.aclose()
        if restriction == "managed_file":
            path.write_text("[mcp_servers]", encoding="utf-8")
        resumed_model = Model(mode)
        resumed = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                api_mode="responses",
                tool_search_mode=mode,
                model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
                mcp_servers=(
                    server("missing-remote" if restriction == "environment" else "local"),
                ),
            ),
            model=resumed_model,
            registry=ToolRegistry(),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
            thread_id=thread,
        )
        try:
            events = [event async for event in resumed.stream("again")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(clients) == 1, (
                "cold startup must not build a client in the wrong environment"
            )
            results = [
                item
                for item in resumed_model.requests[-1].items
                if isinstance(item, ToolResultItem)
            ]
            # Legacy native settings use the ordinary projection too. Stale
            # definitions are pruned without altering the durable archive.
            assert not any(
                item.tool_name == "tool_search" and item.discovered_tools for item in results
            )
            archived = await resumed._repository.load_items(thread)
            assert any(
                isinstance(item, ToolResultItem)
                and item.tool_name == "tool_search"
                and item.discovered_tools
                for item in archived
            )
            assert any(item.is_error and item.tool_name != "tool_search" for item in results)
            assert clients[0].calls == [("write", {"value": 1})]
        finally:
            await resumed.aclose()

    asyncio.run(scenario())


def test_admitted_call_retains_local_lease_after_environment_becomes_unknown(tmp_path, monkeypatch):
    async def scenario():
        runtime, clients, model = await make_runtime(
            tmp_path,
            monkeypatch,
            requirements={},
            servers=(replace(server(), default_tools_approval_mode="prompt"),),
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
            runtime.request_mcp_reconcile((server("missing-remote"),))
            await runtime._mcp_manager.refresh_if_dirty()
            assert len(clients) == 1 and not clients[0].closed and not clients[0].calls
            with pytest.raises(MCPProtocolError, match="unknown environment id"):
                await runtime._mcp_manager.call_tool("docs", "write", {"value": 2})
            release.set()
            events = await asyncio.wait_for(task, 5)
            assert isinstance(events[-1], TurnCompleted)
            assert clients[0].calls == [("write", {"value": 1})]
            assert "remote effect" in repr(model.requests[-1].items)
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()
        assert clients[0].closed

    asyncio.run(scenario())


@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_plugin_declared_environment_is_preserved_and_cannot_start_locally(
    tmp_path, monkeypatch, transport
):
    plugin = tmp_path / "package"
    manifest = plugin / ".codex-plugin/plugin.json"
    manifest.parent.mkdir(parents=True)
    fields = (
        {"command": "never-run"} if transport == "stdio" else {"url": "https://fixture.invalid"}
    )
    manifest.write_text(
        json.dumps(
            {
                "name": "package",
                "mcpServers": {"docs": {**fields, "environment_id": "missing-remote"}},
            }
        ),
        encoding="utf-8",
    )

    async def scenario():
        runtime, clients, _ = await make_runtime(
            tmp_path, monkeypatch, requirements={}, plugins=(plugin,)
        )
        try:
            events = [event async for event in runtime.stream("hello")]
            assert isinstance(events[-1], TurnCompleted)
            assert clients == []
            registration = runtime.mcp_catalog.servers[0]
            assert registration.source.kind == "plugin"
            assert registration.settings.environment_id == "missing-remote"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
