"""Only initialize.instructions changes; search and history must follow the new metadata."""

import asyncio
import json

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
def test_initialize_instructions_drive_discovery_refresh_and_durable_history(
    tmp_path, monkeypatch, mode
):
    async def scenario():
        clients, executed, requests = [], [], []

        def factory(settings):
            word = "amber" if not clients else "cobalt"

            def handler(request):
                if request.method == "DELETE":
                    return httpx.Response(204)
                message = json.loads(request.content)
                method = message["method"]
                if method == "notifications/initialized":
                    return httpx.Response(202)
                if method == "initialize":
                    result = {
                        "capabilities": {},
                        "serverInfo": {"name": "fixture", "version": "1"},
                        "protocolVersion": PROTOCOL_VERSION,
                        "instructions": f"Search {word} records.",
                    }
                elif method == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": "lookup",
                                "description": "Lookup records",
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    }
                elif method == "tools/call":
                    executed.append(word)
                    result = {"content": [{"type": "text", "text": word}]}
                else:
                    raise AssertionError(method)
                return httpx.Response(
                    200,
                    json={"jsonrpc": "2.0", "id": message["id"], "result": result},
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(handler))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)

        class Model:
            async def stream(self, request):
                requests.append(request)
                step = len(requests)
                turn = request.items[-1].turn_id
                word = "amber" if step <= 3 else "cobalt"
                description = next(t.description for t in request.tools if t.name == "tool_search")
                assert f"- docs: Search {word} records.\n" in description
                if step in (1, 4):
                    if step == 4:
                        assert "amber" not in description
                        old_result = next(
                            i
                            for i in request.items
                            if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
                        )
                        assert not old_result.discovered_tools
                        assert "search again" in old_result.content
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": word})
                elif step in (2, 5):
                    found = [
                        i
                        for i in request.items
                        if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
                    ][-1]
                    assert [s.name for s in found.discovered_tools] == ["mcp__docs::lookup"]
                    assert found.discovered_tools[0].source_description == f"Search {word} records."
                    assert "mcp__docs::lookup" in {t.name for t in request.tools}
                    call = ToolCall(new_tool_call_id(), "mcp__docs::lookup", {})
                else:
                    assert step in (3, 6)
                    assert executed == (["amber"] if step == 3 else ["amber", "cobalt"])
                    if step == 3:
                        runtime.request_mcp_refresh()
                    yield ModelCompleted(
                        (
                            AssistantMessageItem(
                                "continue" if step == 3 else "done", turn, new_step_id()
                            ),
                        ),
                        end_turn=step == 6,
                    )
                    return
                yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                api_mode="responses",
                tool_search_mode=mode,
                model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
                mcp_servers=(MCPServerSettings("docs", "http", url="https://docs.test/mcp"),),
            ),
            database_path=tmp_path / "sources.db",
            registry=ToolRegistry(),
            model=Model(),
        )
        try:
            events = [e async for e in runtime.stream("search and refresh the source")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 6
            history = await runtime._repository.load_items(runtime._thread_id)
            found = [
                i.discovered_tools
                for i in history
                if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
            ]
            assert [specs[0].source_description for specs in found] == [
                "Search amber records.",
                "Search cobalt records.",
            ]
        finally:
            await runtime.aclose()
        assert len(clients) == 2 and all(client._client.is_closed for client in clients)

    asyncio.run(scenario())
