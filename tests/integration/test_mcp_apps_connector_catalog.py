"""Ordinary raw tool identity survives cache and explicit connection replacement."""

import asyncio
import json
from copy import deepcopy

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.catalog import MCPCatalog, MCPCatalogSource, MCPRegistration
from corki.mcp.client import HttpMCPClient
from corki.mcp.tool_catalog_cache import CatalogSnapshot, MCPToolCatalogCache
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("delivery", ["live", "pending_cache", "reconnect"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("collision", ["visible", "hidden", "invalid"])
@pytest.mark.parametrize("server", ["docs", "codex_apps"])
def test_collision_identity_and_raw_routes_survive_catalog_delivery(
    tmp_path, monkeypatch, delivery, nested, collision, server
):
    async def scenario():
        routes = [("a", "Gmail_Search"), ("a", "Gmail-Search")]
        if collision == "visible":
            routes.append(("b", "Gmail_Search"))
        definitions = [
            {
                "name": raw,
                "description": "Search invoices",
                "inputSchema": {"type": "object"},
                "_meta": {"connector_id": connector, "connector_name": "Gmail"},
            }
            for connector, raw in routes
        ]
        duplicate = deepcopy(definitions[0])
        duplicate["description"] = "Duplicate must not win"
        definitions.append(duplicate)
        hidden = deepcopy(definitions[0])
        hidden["name"] = "Gmail_Hidden"
        hidden["_meta"].update(connector_id="hidden", ui={"visibility": ["app"]})
        if collision != "invalid":
            definitions.append(hidden)
        invalid = deepcopy(definitions[0])
        invalid["name"] = "Gmail_Invalid"
        invalid["_meta"]["connector_id"] = "invalid"
        invalid["inputSchema"] = {"type": "object", "required": "invalid"}
        if collision != "hidden":
            definitions.append(invalid)
        original = deepcopy(definitions)
        clients, calls, discovered = [], [], []
        release, started = asyncio.Event(), asyncio.Event()
        phase = [
            definitions
            if delivery in ("live", "pending_cache")
            else [
                {"name": "old", "inputSchema": {"type": "object"}, "_meta": {"connector_id": "old"}}
            ]
        ]

        def factory(settings):
            index = len(clients)

            async def respond(request):
                packet = json.loads(request.content)
                if packet["method"] == "notifications/initialized":
                    return httpx.Response(202)
                if packet["method"] == "initialize":
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "fixture", "version": str(index)},
                    }
                elif packet["method"] == "tools/list":
                    started.set()
                    if delivery == "pending_cache":
                        await release.wait()
                    result = {"tools": phase[0]}
                else:
                    assert packet["method"] == "tools/call"
                    calls.append((index, packet["params"]["name"]))
                    result = {"content": [{"type": "text", "text": "mail result"}]}
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(respond))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        registry = ToolRegistry()

        class Model:
            steps = 0

            async def stream(self, request):
                self.steps += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if self.steps == 1:
                    discovered.extend(
                        t for t in registry.specs() if t.description == "Search invoices"
                    )
                    if delivery == "pending_cache":
                        assert started.is_set() and not release.is_set()
                        assert not runtime._mcp_manager._preparation.tasks[server].done()
                    release.set()
                if self.steps == 1 and not nested:
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "Search invoices"})
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                elif self.steps == (1 if nested else 2):
                    if nested:
                        call = ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments="for (const t of ALL_TOOLS.filter(t => "
                            't.description.includes("Search invoices"))) '
                            "{text(await tools[t.name]({}));}",
                        )
                        items = (ToolCallItem(call, turn, step),)
                    else:
                        search = [i for i in request.items if getattr(i, "discovered_tools", ())][
                            -1
                        ]
                        assert {t.name for t in search.discovered_tools} == {
                            t.name for t in discovered
                        }
                        items = tuple(
                            ToolCallItem(ToolCall(new_tool_call_id(), t.name, {}), turn, step)
                            for t in search.discovered_tools
                        )
                    yield ModelCompleted(items)
                else:
                    results = [i for i in request.items if isinstance(i, ToolResultItem)]
                    assert "mail result" in results[-1].content and not results[-1].is_error
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        settings = MCPServerSettings(server, "http", url="https://fixture.invalid")
        cache = MCPToolCatalogCache()
        entry = cache.context(settings)
        if delivery == "pending_cache":
            entry.publish_if_newest(entry.begin_fetch(), CatalogSnapshot(tuple(definitions), None))
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                execution_permissions=None,
                mcp_servers=(settings,),
                tool_mode="code_mode_only" if nested else "direct",
                tool_search_mode="disabled" if nested else "compatible",
            ),
            mcp_catalog=MCPCatalog(
                (MCPRegistration(settings, MCPCatalogSource("compatibility", "fixture")),)
            ),
            mcp_tool_catalog_cache=cache,
            registry=registry,
            model=Model(),
            database_path=tmp_path / "catalog.db",
            home_path=tmp_path / "home",
        )
        try:
            await runtime._ensure_ready()
            await started.wait()
            if delivery == "reconnect":
                await runtime._mcp_manager.start()
                assert registry.spec(f"mcp__{server}::old") is not None
                phase[0] = definitions
                runtime.request_mcp_refresh()
                await runtime._mcp_manager.start()
                assert registry.spec("mcp__codex_apps::old") is None
            events = [e async for e in runtime.stream("search invoices and call each tool")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(discovered) == 2
            assert len({t.name for t in discovered}) == 2
            assert all(t.name.startswith(f"mcp__{server}::") for t in discovered)
            index = 1 if delivery == "reconnect" else 0
            assert sorted(calls) == sorted((index, raw) for raw in {raw for _, raw in routes})
            catalog = await runtime._mcp_manager.list_tool_catalog()
            assert len(catalog) == len({d["name"] for d in definitions})
            assert all(e.connector_id is None for e in catalog)
            assert definitions == original
            assert entry.current_tools_or().definitions == tuple(original)
            catalog[0].definition["_meta"]["connector_name"] = "mutated host copy"
            assert entry.current_tools_or().definitions == tuple(original)
        finally:
            release.set()
            await runtime.aclose()
        assert all(c.is_closed for c in clients)

    asyncio.run(scenario())
