"""Config reconciliation shares a real HTTP session; explicit refresh replaces it."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import PROTOCOL_VERSION, HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["native", "compatible"])
def test_runtime_reconciles_policy_without_relisting_then_explicitly_reconnects(
    tmp_path, monkeypatch, mode
):
    async def scenario():
        clients, calls, lists, closed, requests = [], [], [], [], []

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
                    lists.append(session)
                    result = {
                        "tools": [
                            {
                                "name": name,
                                "description": "needle",
                                "inputSchema": {"type": "object"},
                            }
                            for name in ("read", "write")
                        ]
                    }
                elif method == "tools/call":
                    assert request.headers["Mcp-Session-Id"] == session
                    calls.append((session, message["params"]["name"]))
                    result = {"content": [{"type": "text", "text": "value " * 300}]}
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
        settings = MCPServerSettings(
            "docs", "http", url="https://fixture.test", tool_output_token_limits=(("read", 500),)
        )
        changed = replace(
            settings, enabled_tools=("read",), tool_output_token_limits=(("read", 20),)
        )

        class Model:
            async def stream(self, request):
                requests.append(request)
                number = len(requests)
                turn, step = request.items[-1].turn_id, new_step_id()
                results = [i for i in request.items if isinstance(i, ToolResultItem)]
                if number == 1:
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "needle"})
                elif number == 2:
                    assert len(results[-1].discovered_tools) == 2
                    runtime.request_mcp_reconcile((changed,))
                    call = ToolCall(new_tool_call_id(), "mcp__docs::read", {})
                elif number == 3:
                    assert calls == [("0", "read")] and lists == ["0"] and closed == []
                    assert results[-1].model_output_projected and len(results[-1].content) < 500
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "needle"})
                elif number == 4:
                    assert [s.name for s in results[-1].discovered_tools] == ["mcp__docs::read"]
                    runtime.request_mcp_refresh()
                    call = ToolCall(new_tool_call_id(), "mcp__docs::read", {})
                else:
                    assert number == 5 and calls == [("0", "read"), ("1", "read")]
                    assert lists == ["0", "1"] and len(clients) == 2
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                mcp_servers=(settings,),
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
            outputs = [
                i for i in raw if isinstance(i, ToolResultItem) and i.tool_name == "mcp__docs::read"
            ]
            # Durable fallback includes the established MCP 20% serialization allowance.
            assert [i.fallback_token_limit_override for i in outputs] == [24, 24]
            assert [
                len(i.discovered_tools)
                for i in raw
                if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
            ] == [2, 1]
        finally:
            await runtime.aclose()
        assert closed == ["0", "1"] and all(c._client.is_closed for c in clients)

        class ColdModel:
            async def stream(self, request):
                found = [
                    i
                    for i in request.items
                    if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
                ]
                assert [len(i.discovered_tools) for i in found] == [1, 1]
                assert "mcp__docs::write" not in {t.name for t in request.tools}
                assert calls == [("0", "read"), ("1", "read")]
                yield ModelCompleted(
                    (AssistantMessageItem("cold", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        cold = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                mcp_servers=(changed,),
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
        assert closed == ["0", "1", "2"] and calls == [("0", "read"), ("1", "read")]

    asyncio.run(scenario())
