"""Trusted plugin display names participate in actual search and tool invocation."""

import asyncio
import json
from dataclasses import replace

import pytest
from test_mcp_pending_reuse import PendingClient

from corki.config import CorkiSettings, MCPServerSettings
from corki.config.model_context import ModelContextInfo
from corki.core import LangGraphRuntime
from corki.mcp.catalog import MCPCatalog, MCPCatalogSource, MCPRegistration
from corki.mcp.manager import MCPManager
from corki.mcp.tool_catalog_cache import MCPToolCatalogCache
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["native", "compatible"])
@pytest.mark.parametrize("kind", ["legacy", "agent"])
def test_plugin_display_name_search_call_observation(tmp_path, monkeypatch, mode, kind):
    async def scenario():
        root = tmp_path / ".corki/plugins/sample/.codex-plugin"
        root.mkdir(parents=True)
        (root / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "sample",
                    "interface": {"displayName": "  Nimbus Records  "},
                    "mcpServers": {"docs": {"url": "https://display.test"}},
                }
            )
        )
        if kind == "agent":
            (root.parent / "plugin.json").write_text(
                json.dumps(
                    {
                        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                        "name": "sample",
                        "extensions": {
                            "com.openai": {"interface": {"displayName": "Nimbus Records"}}
                        },
                    }
                )
            )
            (root.parent / "mcp.json").write_text(
                json.dumps(
                    {
                        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
                        "mcpServers": {
                            "docs": {"type": "streamable-http", "url": "https://display.test"}
                        },
                    }
                )
            )
        clients, requests = [], []

        def factory(settings):
            client = PendingClient(settings, "initialize")
            client.release.set()
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        monkeypatch.setattr(
            "corki.mcp.manager.HttpMCPClient", lambda settings, **kwargs: factory(settings)
        )

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                results = [i for i in request.items if isinstance(i, ToolResultItem)]
                display = "Nimbus Records" if len(requests) <= 3 else "Stratus Archive"
                phase = (len(requests) - 1) % 3
                if phase == 0:
                    if len(requests) == 1:
                        assert not any(t.name.startswith("mcp__docs") for t in request.tools)
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": display})
                elif phase == 1:
                    found = results[-1].discovered_tools
                    assert len(found) == 2
                    assert all(
                        f"This tool is part of plugin `{display}`." in t.description for t in found
                    )
                    call = ToolCall(new_tool_call_id(), "mcp__docs::new", {})
                else:
                    assert len(requests) in (3, 6) and not results[-1].is_error
                    assert clients[0].calls == ["new"] * (len(requests) // 3)
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                plugin_dirs=(tmp_path / ".corki/plugins",),
                skills_enabled=False,
                execution_permissions=None,
                api_mode="responses",
                tool_search_mode=mode,
                model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "display.db",
        )
        try:
            events = [event async for event in runtime.stream("Find Nimbus Records")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert runtime._registry.snapshot().mcp_plugin_id("mcp__docs::new") == "sample"
            (registration,) = runtime.mcp_catalog.servers
            runtime.request_mcp_catalog(
                MCPCatalog(
                    (
                        replace(
                            registration,
                            source=replace(registration.source, display_name="Stratus Archive"),
                        ),
                    )
                )
            )
            events = [event async for event in runtime.stream("Find Stratus Archive")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 6 and clients[0].starts == clients[0].lists == 1
        finally:
            await runtime.aclose()
        assert len(clients) == 1 and clients[0].closes == 1

    asyncio.run(scenario())


def test_display_refresh_and_cross_manager_cache_use_only_current_winner(monkeypatch):
    async def scenario():
        settings = MCPServerSettings("docs", "http", url="https://display.test")
        cache = MCPToolCatalogCache()
        clients = []

        class Client(PendingClient):
            async def list_tools(self):
                self.lists += 1
                await self.pause("list")
                return [
                    {
                        "name": "read",
                        "description": "Raw description",
                        "_meta": {"plugin_display_names": ["Forged"]},
                    }
                ]

        def factory(settings):
            client = Client(settings, "list")
            if not clients:
                client.release.set()
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        source = MCPCatalogSource("plugin", "stable-id", display_name="Local Nimbus")
        catalog = MCPCatalog((MCPRegistration(settings, source),))
        first_registry, second_registry = ToolRegistry(), ToolRegistry()
        first = MCPManager((settings,), first_registry, catalog=catalog, tool_catalog_cache=cache)
        second = MCPManager(
            (settings,),
            second_registry,
            catalog=MCPCatalog(
                (MCPRegistration(settings, replace(source, display_name="Other Nimbus")),)
            ),
            tool_catalog_cache=cache,
        )
        try:
            await first.start()
            frozen = first_registry.snapshot()
            await second.capture_tools(optional_startup_grace_ms=1)
            second_spec = next(s for s in second_registry.specs() if s.name == "mcp__docs::read")
            assert "Other Nimbus" in second_spec.description
            assert (
                "Local Nimbus" not in second_spec.search_text
                and "Forged" not in second_spec.search_text
            )
            assert not clients[1].release.is_set()
            source = replace(source, kind="selected_plugin", display_name="Executor Nimbus")
            first.request_catalog(MCPCatalog((MCPRegistration(settings, source),)))
            await first.capture_tools(optional_startup_grace_ms=1)
            current = next(s for s in first_registry.specs() if s.name == "mcp__docs::read")
            assert (
                current.description
                == "Raw description. This tool is part of plugin `Executor Nimbus`."
            )
            assert (
                "Local Nimbus"
                in next(s for s in frozen.specs() if s.name == current.name).description
            )
            assert first_registry.snapshot().mcp_plugin_id(current.name) == "stable-id"
            assert clients[0].starts == clients[0].lists == 1
            snapshot = cache.context(settings).current_tools_or()
            assert snapshot.definitions[0]["description"] == "Raw description"
            first.request_catalog(
                MCPCatalog((MCPRegistration(settings, source), MCPRegistration(settings)))
            )
            await first.capture_tools(optional_startup_grace_ms=1)
            plain = next(s for s in first_registry.specs() if s.name == current.name)
            assert plain.description == "Raw description" and "Nimbus" not in plain.search_text
            assert first_registry.snapshot().mcp_plugin_id(plain.name) is None
        finally:
            await first.aclose()
            await second.aclose()
        assert all(client.closes == 1 for client in clients)

    asyncio.run(scenario())
