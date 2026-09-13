"""MCP configuration policy reaches discovery, durable history and real call admission."""

import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import PROTOCOL_VERSION, HttpMCPClient
from corki.mcp.names import normalize_tool_names
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["native", "compatible"])
def test_loaded_tool_revocation_returns_observation_and_allows_new_search(
    tmp_path, monkeypatch, mode
):
    async def scenario():
        calls, closed, clients, requests = [], [], [], []

        def factory(settings):
            session = str(len(clients))

            def handler(request):
                if request.method == "DELETE":
                    closed.append(session)
                    return httpx.Response(204)
                message = json.loads(request.content)
                method = message["method"]
                if method == "notifications/initialized":
                    return httpx.Response(202)
                if method == "initialize":
                    result = {
                        "serverInfo": {"name": "fixture", "version": "1"},
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                    }
                elif method == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": name,
                                "description": "needle",
                                "inputSchema": {"type": "object"},
                            }
                            for name in ("tool-name", "tool_name")
                        ]
                    }
                elif method == "tools/call":
                    calls.append((session, message["params"]["name"]))
                    result = {"content": [{"type": "text", "text": "allowed result"}]}
                else:
                    raise AssertionError(method)
                return httpx.Response(
                    200,
                    headers={"mcp-session-id": session},
                    json={
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "result": result,
                    },
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(handler))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        original = MCPServerSettings.from_mapping(
            "docs", {"url": "https://fixture.test", "transport": "http"}
        )
        restricted = MCPServerSettings.from_mapping(
            "docs",
            {
                "url": "https://fixture.test",
                "transport": "http",
                "disabled_tools": ["tool-name"],
            },
        )
        denied = next(
            n.canonical
            for n in normalize_tool_names((("docs", "tool-name"), ("docs", "tool_name")))
            if n.remote == "tool-name"
        )

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                number = len(requests)
                results = [i for i in request.items if isinstance(i, ToolResultItem)]
                if number == 1:
                    assert not any(t.exposure.is_deferred for t in request.tools)
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "needle"})
                elif number == 2:
                    assert len(results[-1].discovered_tools) == 2
                    runtime.request_mcp_refresh((restricted,))
                    call = ToolCall(new_tool_call_id(), denied, {})
                elif number == 3:
                    observation = next(i for i in results if i.tool_name == denied)
                    assert observation.is_error and "disabled" in observation.content
                    assert calls == []
                    old_search = next(i for i in results if i.tool_name == "tool_search")
                    assert not old_search.discovered_tools
                    assert "search again" in old_search.content
                    assert denied not in {t.name for t in request.tools}
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "needle"})
                elif number == 4:
                    assert [s.name for s in results[-1].discovered_tools] == [
                        "mcp__docs::tool_name"
                    ]
                    call = ToolCall(new_tool_call_id(), "mcp__docs::tool_name", {})
                else:
                    assert number == 5 and calls == [("1", "tool_name")]
                    assert not results[-1].is_error and "allowed result" in results[-1].content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                mcp_servers=(original,),
                api_mode="responses",
                tool_search_mode=mode,
                model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
        )
        try:
            events = [e async for e in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            raw = await runtime._repository.load_items(runtime._thread_id)
            searches = [
                i for i in raw if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
            ]
            assert [len(i.discovered_tools) for i in searches] == [2, 1]
            assert len(requests) == 5
        finally:
            await runtime.aclose()
        assert closed == ["0", "1"] and all(c._client.is_closed for c in clients)

        class ColdModel:
            async def stream(self, request):
                old = [
                    i
                    for i in request.items
                    if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
                ]
                assert [len(i.discovered_tools) for i in old] == [0, 1]
                assert denied not in {t.name for t in request.tools}
                assert calls == [("1", "tool_name")]
                yield ModelCompleted(
                    (AssistantMessageItem("cold", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        cold = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                mcp_servers=(restricted,),
                api_mode="responses",
                tool_search_mode=mode,
                model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
            ),
            model=ColdModel(),
            registry=ToolRegistry(),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
            thread_id=runtime._thread_id,
        )
        try:
            events = [e async for e in cold.stream("continue")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            after = await cold._repository.load_items(cold._thread_id)
            assert after[: len(raw)] == raw
        finally:
            await cold.aclose()
        assert closed == ["0", "1", "2"] and calls == [("1", "tool_name")]

    asyncio.run(scenario())
