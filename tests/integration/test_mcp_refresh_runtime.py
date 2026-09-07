"""Explicit refresh traverses the real graph and HTTP MCP lifecycle, without a model API."""

import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import PROTOCOL_VERSION, HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["native", "compatible"])
@pytest.mark.parametrize("initial_empty", [False, True])
def test_mcp_refresh_preserves_current_step_then_updates_search_and_history(
    tmp_path, monkeypatch, mode, initial_empty
):
    async def scenario():
        clients, executed, closed = [], [], []

        def client_factory(settings):
            word = "amber" if settings.name == "docs" else "cobalt"
            value_type = "string" if settings.name == "docs" else "integer"
            session_id = f"{settings.name}-{len(clients)}"

            def handler(request):
                if request.method == "DELETE":
                    closed.append(session_id)
                    return httpx.Response(204)
                message = json.loads(request.content)
                method = message["method"]
                if method == "notifications/initialized":
                    return httpx.Response(202)
                if method == "initialize":
                    result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}}}
                elif method == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": "lookup",
                                "description": word,
                                "inputSchema": {
                                    "type": "object",
                                    "required": ["value"],
                                    "properties": {"value": {"type": value_type}},
                                },
                            }
                        ]
                    }
                elif method == "tools/call":
                    assert request.headers["Mcp-Session-Id"] == session_id
                    value = message["params"]["arguments"]["value"]
                    assert isinstance(value, str if value_type == "string" else int)
                    executed.append((settings.name, value))
                    result = {"content": [{"type": "text", "text": "ran " + word}]}
                else:
                    raise AssertionError(method)
                return httpx.Response(
                    200,
                    headers={"mcp-session-id": session_id},
                    json={"jsonrpc": "2.0", "id": message["id"], "result": result},
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(handler))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", client_factory)
        docs = MCPServerSettings("docs", "http", url="https://docs.test/mcp")
        notes = MCPServerSettings("notes", "http", url="https://notes.test/mcp")
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                step = len(requests) - int(initial_empty)
                turn = request.items[-1].turn_id
                if step == 0:
                    assert not any(t.name == "tool_search" for t in request.tools)
                    yield ModelCompleted((AssistantMessageItem("idle", turn, new_step_id()),))
                    return
                if step in (1, 3):
                    description = next(
                        t.description for t in request.tools if t.name == "tool_search"
                    )
                    source = "docs" if step == 1 else "notes"
                    assert f"following sources:\n- {source}\nSome of the tools" in description
                    if step == 3:
                        old_search = next(
                            i
                            for i in request.items
                            if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
                        )
                        if mode == "native":
                            assert old_search.discovered_tools[0].name == "mcp__docs__lookup"
                        else:
                            assert not old_search.discovered_tools
                            assert "search again" in old_search.content
                        old_call = next(
                            i
                            for i in request.items
                            if isinstance(i, ToolResultItem) and i.tool_name == "mcp__docs__lookup"
                        )
                        assert old_call.is_error and "unknown MCP server" in old_call.content
                        assert executed == []
                        assert not any(t.name == "mcp__docs__lookup" for t in request.tools)
                    call = ToolCall(
                        new_tool_call_id(),
                        "tool_search",
                        {"query": "amber" if step == 1 else "cobalt"},
                    )
                elif step in (2, 4):
                    found = [
                        i
                        for i in request.items
                        if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
                    ][-1]
                    name = "mcp__docs__lookup" if step == 2 else "mcp__notes__lookup"
                    assert [s.name for s in found.discovered_tools] == [name]
                    if step == 2:
                        runtime.request_mcp_refresh((notes,))
                        # Queuing is synchronous, but the upcoming MCP call MUST
                        # resolve refreshed state instead of using the removed server.
                        assert len(clients) == 1
                    call = ToolCall(new_tool_call_id(), name, {"value": "old" if step == 2 else 42})
                elif step == 5:
                    assert executed == [("notes", 42)]
                    runtime.request_mcp_refresh(())
                    yield ModelCompleted(
                        (AssistantMessageItem("remove", turn, new_step_id()),), end_turn=False
                    )
                    return
                else:
                    assert step == 6
                    assert not any(
                        t.name == "tool_search" or t.name.startswith("mcp__") for t in request.tools
                    )
                    assert any(
                        i.discovered_tools
                        for i in request.items
                        if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
                    ) == (mode == "native")
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                mcp_servers=() if initial_empty else (docs,),
                tool_search_mode=mode,
                api_mode="responses",
            ),
            database_path=tmp_path / "refresh.db",
            registry=ToolRegistry(),
            model=Model(),
        )
        try:
            if initial_empty:
                idle = [e async for e in runtime.stream("start with no servers")]
                assert isinstance(idle[-1], TurnCompleted), idle[-1]
                runtime.request_mcp_refresh((docs,))
            events = [e async for e in runtime.stream("discover, replace, and remove")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            raw = await runtime._repository.load_items(runtime._thread_id)
            searches = [
                i for i in raw if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
            ]
            assert [i.discovered_tools[0].name for i in searches] == [
                "mcp__docs__lookup",
                "mcp__notes__lookup",
            ]
        finally:
            await runtime.aclose()
        assert len(requests) == 6 + int(initial_empty) and closed == ["docs-0", "notes-1"]
        assert all(client._client.is_closed for client in clients)

    asyncio.run(scenario())
