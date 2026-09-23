"""All sources use ordinary capacity and preserve the healthy tool loop."""

import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.catalog import MCPCatalog, MCPCatalogSource, MCPRegistration
from corki.mcp.client import HttpMCPClient
from corki.mcp.tool_catalog_cache import MCPToolCatalogCache
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize(
    "kind,name,count,accepted",
    [
        ("compatibility", "codex_apps", 2049, False),
        ("compatibility", "codex_apps", 8192, False),
        ("compatibility", "codex_apps", 8193, False),
        ("config", "codex_apps", 2049, False),
        ("extension", "codex_apps", 2049, False),
        ("compatibility", "ordinary", 2049, False),
        ("config", "codex_apps", 2048, True),
        ("compatibility", "codex_apps", 2048, True),
        ("hosted", "codex_apps", 2048, True),
        ("hosted", "codex_apps", 2049, False),
        ("hosted", "codex_apps", 8192, False),
        ("hosted", "codex_apps", 8193, False),
    ],
)
def test_hosted_catalog_limit_reaches_actual_runtime(
    tmp_path, monkeypatch, nested, kind, name, count, accepted
):
    async def scenario():
        clients, calls, listings = [], [], []
        chosen = name if accepted else "fallback"
        # A failed Apps startup now has one immediate background retry. Keep
        # subsequent eligibility deterministic without removing the real retry.

        def factory(server):
            async def respond(request):
                packet = json.loads(request.content)
                method = packet["method"]
                if method == "notifications/initialized":
                    return httpx.Response(202)
                if method == "initialize":
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": server.name, "version": "1"},
                    }
                elif method == "tools/list":
                    listings.append(server.name)
                    tools = [
                        {"name": f"irrelevant_{n}", "inputSchema": {"type": "object"}}
                        for n in range((count if server.name == name else 1) - 1)
                    ]
                    tools.append(
                        {
                            "name": "needle",
                            "description": f"needle_{server.name}",
                            "_meta": {"connector_id": "fixture"},
                            "inputSchema": {"type": "object"},
                        }
                    )
                    result = {"tools": tools, "_meta": {"host_owned_apps": True, "max_tools": 8192}}
                else:
                    assert method == "tools/call"
                    calls.append(server.name)
                    result = {"content": [{"type": "text", "text": "called-" + server.name}]}
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
                )

            client = HttpMCPClient(server, transport=httpx.MockTransport(respond))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)

        class Model:
            steps = 0

            async def stream(self, request):
                self.steps += 1
                turn = request.items[-1].turn_id
                if self.steps == 1 and not nested:
                    call = ToolCall(
                        new_tool_call_id(), "tool_search", {"query": f"needle_{chosen}"}
                    )
                elif self.steps == 1 + int(not nested):
                    if nested:
                        description = next(t.description for t in request.tools if t.name == "exec")
                        assert f"mcp__{chosen}__needle" in description
                        call = ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments=f"text(await tools.mcp__{chosen}__needle({{}}));",
                        )
                    else:
                        assert f"mcp__{chosen}::needle" in {t.name for t in request.tools}
                        call = ToolCall(new_tool_call_id(), f"mcp__{chosen}::needle", {})
                else:
                    output = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert not output.is_error and f"called-{chosen}" in output.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))

            async def aclose(self):
                pass

        server = MCPServerSettings(
            name, "http", url=f"https://{name}.invalid", enabled_tools=("needle",)
        )
        fallback = MCPServerSettings("fallback", "http", url="https://fallback.invalid")
        catalog = MCPCatalog(
            (
                MCPRegistration(
                    server,
                    MCPCatalogSource(
                        "extension" if kind == "hosted" else kind,
                        None if kind == "config" else "fixture",
                        **({"host_owned_apps": True} if kind == "hosted" else {}),
                    ),
                ),
                MCPRegistration(fallback),
            )
        )
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                execution_permissions=None,
                tool_search_mode="disabled" if nested else "compatible",
                tool_mode="code_mode_only" if nested else "direct",
            ),
            model=Model(),
            registry=ToolRegistry(),
            mcp_catalog=catalog,
            database_path=tmp_path / "capacity.db",
            home_path=tmp_path / "home",
            mcp_tool_catalog_cache=MCPToolCatalogCache(),
        )
        try:
            events = [event async for event in runtime.stream("lookup the catalog needle")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert calls == [chosen]
            assert sorted(listings) == sorted([name, "fallback"])
            assert bool(runtime._mcp_manager.warnings) is (not accepted)
        finally:
            await runtime.aclose()
        assert all(client.is_closed for client in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("initial_hosted", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_legacy_hosted_source_change_preserves_ordinary_transport(
    tmp_path, monkeypatch, initial_hosted, nested
):
    async def scenario():
        clients, calls = [], []
        server = MCPServerSettings("codex_apps", "http", url="https://fixture.invalid")

        def catalog(hosted):
            return MCPCatalog(
                (
                    MCPRegistration(
                        server, MCPCatalogSource("extension", "fixture", host_owned_apps=hosted)
                    ),
                )
            )

        def factory(settings):
            version = len(clients)

            async def respond(request):
                packet = json.loads(request.content)
                method = packet["method"]
                if method == "notifications/initialized":
                    return httpx.Response(202)
                if method == "initialize":
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "fixture", "version": "1"},
                    }
                elif method == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": "needle",
                                "inputSchema": {"type": "object"},
                                "_meta": {"connector_id": "fixture"},
                            }
                        ]
                    }
                else:
                    assert method == "tools/call"
                    calls.append(version)
                    result = {"content": [{"type": "text", "text": f"version-{version}"}]}
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(respond))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)

        class Model:
            steps = 0

            async def stream(self, request):
                self.steps += 1
                turn = request.items[-1].turn_id
                if self.steps == 1:
                    assert len(clients) == 1
                    runtime.request_mcp_catalog(catalog(not initial_hosted))
                    call = (
                        ToolCall(new_tool_call_id(), "mcp__codex_apps::needle", {})
                        if not nested
                        else ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments="text(await tools.mcp__codex_apps__needle({}));",
                        )
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))
                else:
                    output = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert not output.is_error and "version-0" in output.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                execution_permissions=None,
                tool_search_mode="disabled",
                tool_mode="code_mode_only" if nested else "direct",
            ),
            registry=ToolRegistry(),
            model=Model(),
            mcp_catalog=catalog(initial_hosted),
            database_path=tmp_path / "replacement.db",
            home_path=tmp_path / "home",
            mcp_tool_catalog_cache=MCPToolCatalogCache(),
        )
        try:
            events = [event async for event in runtime.stream("refresh source before call")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert calls == [0] and len(clients) == 1
        finally:
            await runtime.aclose()
        assert all(client.is_closed for client in clients)

    asyncio.run(scenario())
