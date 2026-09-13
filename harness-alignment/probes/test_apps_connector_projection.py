"""Connector metadata changes Apps model identities, never raw RPC routing."""

import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("server", ["codex_apps", "ordinary"])
@pytest.mark.parametrize("metadata", ["primary", "aliases", "id_only"])
@pytest.mark.parametrize("check", ["name", "description", "source"])
def test_connector_projection_reaches_search_definition_and_raw_rpc(
    tmp_path, monkeypatch, server, metadata, check
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
        raw_name = "connector_gmail_search" if metadata == "id_only" else "Gmail_Search"

        def factory(settings):
            async def respond(request):
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
                if self.calls == 1:
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "Search invoices"})
                elif self.calls == 2:
                    search = [item for item in request.items if isinstance(item, ToolResultItem)][
                        -1
                    ]
                    found.extend(search.discovered_tools)
                    assert len(found) == 1
                    assert found[0].name in {tool.name for tool in request.tools}
                    call = ToolCall(new_tool_call_id(), found[0].name, {})
                else:
                    result = [item for item in request.items if isinstance(item, ToolResultItem)][
                        -1
                    ]
                    assert not result.is_error and "mail result" in result.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        declaration = MCPServerSettings(server, "http", url="https://fixture.invalid")
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                execution_permissions=None,
                mcp_servers=(declaration,),
                tool_search_mode="compatible",
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "projection.db",
            home_path=tmp_path / "home",
        )
        try:
            events = [event async for event in runtime.stream("search invoices")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert calls == [raw_name], "model normalization must never rewrite the wire tool name"
            if server == "codex_apps":
                namespace = "mcp__codex_apps" + ("" if metadata == "id_only" else "__gmail")
                expected_name = namespace + "::_search"
                expected_description = "" if metadata == "id_only" else "Mail connector"
                expected_source = "codex_apps" if metadata == "id_only" else "Gmail"
            else:
                expected_name = "mcp__ordinary::" + raw_name
                expected_description = "Server instructions"
                expected_source = "ordinary"
            if check == "name":
                assert found[0].name == expected_name
            elif check == "description":
                assert found[0].namespace_description == expected_description
            else:
                assert found[0].source == expected_source
        finally:
            await runtime.aclose()
        assert all(client.is_closed for client in clients)

    asyncio.run(scenario())
