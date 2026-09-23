"""Opaque connector metadata cannot change ordinary MCP identities or RPC routing."""

import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("server", ["codex_apps", "ordinary"])
@pytest.mark.parametrize("metadata", ["primary", "aliases", "id_only", "name_only", "none"])
@pytest.mark.parametrize("mode", ["compatible", "native", "code_mode"])
def test_connector_projection_reaches_search_definition_and_raw_rpc(
    tmp_path, monkeypatch, server, metadata, mode
):
    async def scenario():
        clients, calls, found = [], [], []
        meta = {"connector_id": "connector_gmail"}
        if metadata == "primary":
            meta.update(connector_name=" Gmail ", connector_description=" Mail connector ")
        elif metadata == "aliases":
            meta.update(
                connector_name=False,
                connector_display_name=" Gmail ",
                connector_description="",
                connectorDescription=" Mail connector ",
            )
        elif metadata == "name_only":
            meta = {"connector_name": "Gmail"}
        elif metadata == "none":
            meta = {}
        raw_name = "connector_gmail_search" if metadata == "id_only" else "Gmail_Search"

        def factory(settings):
            async def respond(request):
                assert str(request.url).rstrip("/") == "https://fixture.invalid"
                packet = json.loads(request.content)
                if packet["method"] == "notifications/initialized":
                    return httpx.Response(202)
                if packet["method"] == "initialize":
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "fixture", "version": "1"},
                        "instructions": "Server instructions",
                    }
                elif packet["method"] == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": raw_name,
                                "title": "Gmail_Search mail",
                                "description": "Search invoices",
                                "_meta": meta,
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    }
                else:
                    assert packet["method"] == "tools/call"
                    calls.append(packet["params"]["name"])
                    result = {"content": [{"type": "text", "text": "mail result"}]}
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(respond))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)

        class Model:
            calls = 0

            async def stream(self, request):
                self.calls += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if self.calls in (1, 4) and mode == "code_mode":
                    if self.calls == 1:
                        found.extend(
                            t for t in registry.specs() if t.description == "Search invoices"
                        )
                    call = ToolCall(
                        new_tool_call_id(),
                        "exec",
                        None,
                        input_kind="freeform",
                        raw_arguments="const tool = ALL_TOOLS.find("
                        't => t.description.includes("Search invoices"));'
                        "text(tool.name); text(await tools[tool.name]({}));",
                    )
                elif self.calls == 1:
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "Search invoices"})
                elif self.calls == 2 and mode != "code_mode":
                    search = [
                        item for item in request.items if getattr(item, "discovered_tools", ())
                    ][-1]
                    found.extend(search.discovered_tools)
                    assert len(found) == 1
                    assert found[0].name in {tool.name for tool in request.tools}
                    call = ToolCall(new_tool_call_id(), found[0].name, {})
                elif self.calls == 4:
                    assert any(getattr(i, "discovered_tools", ()) for i in request.items)
                    call = ToolCall(new_tool_call_id(), found[0].name, {})
                else:
                    result = [item for item in request.items if isinstance(item, ToolResultItem)][
                        -1
                    ]
                    assert not result.is_error and "mail result" in result.content, result.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        declaration = MCPServerSettings(server, "http", url="https://fixture.invalid")
        registry = ToolRegistry()

        async def make_runtime(model, thread_id=None):
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    tmp_path,
                    skills_enabled=False,
                    execution_permissions=None,
                    mcp_servers=(declaration,),
                    tool_search_mode="disabled" if mode == "code_mode" else mode,
                    tool_mode="code_mode_only" if mode == "code_mode" else "direct",
                    api_mode="responses",
                    model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
                ),
                model=model,
                registry=registry,
                database_path=tmp_path / "projection.db",
                home_path=tmp_path / "home",
                thread_id=thread_id,
            )

        model = Model()
        runtime = await make_runtime(model)
        try:
            events = [event async for event in runtime.stream("search invoices")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert calls == [raw_name], "model normalization must never rewrite the wire tool name"
            expected_name = f"mcp__{server}::{raw_name}"
            expected_description = "Server instructions"
            expected_source = server
            assert found[0].name == expected_name
            assert found[0].namespace_description == expected_description
            assert found[0].source == expected_source
            catalog = await runtime._mcp_manager.list_tool_catalog()
            assert len(catalog) == 1 and catalog[0].name == expected_name
            assert catalog[0].definition["name"] == raw_name
            assert catalog[0].definition["title"] == "Gmail_Search mail"
            assert catalog[0].definition["_meta"] == meta, "host raw catalog stays detached"
            assert catalog[0].connector_name is None
            assert catalog[0].connector_id is None
            model.calls = 3
            events = [event async for event in runtime.stream("call again in the same thread")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert calls == [raw_name, raw_name]
            thread_id = runtime.thread_id
            await runtime.aclose()
            model = Model()
            model.calls = 3
            registry = ToolRegistry()
            runtime = await make_runtime(model, thread_id)
            events = [event async for event in runtime.stream("call after cold restore")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert calls == [raw_name, raw_name, raw_name]
        finally:
            await runtime.aclose()
            assert all(client.is_closed for client in clients)
        assert all(client.is_closed for client in clients)

    asyncio.run(scenario())
