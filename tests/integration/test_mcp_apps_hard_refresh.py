"""Hard refresh reaches fresh Runtime discovery and exact-client tool execution."""

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
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("during_sampling", [False, True])
def test_hard_refresh_updates_next_step_without_confusing_reconnect(
    tmp_path, monkeypatch, server, nested, during_sampling
):
    async def scenario():
        clients, lists, calls, resources = [], [], [], []
        phase = ["old"]

        def factory(settings):
            index = len(clients)

            async def respond(request):
                assert str(request.url).rstrip("/") == "https://fixture.invalid"
                packet = json.loads(request.content)
                method = packet["method"]
                if method == "notifications/initialized":
                    return httpx.Response(202)
                if method == "initialize":
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "fixture", "version": str(index)},
                    }
                elif method == "tools/list":
                    lists.append(index)
                    result = {
                        "tools": [
                            {
                                "name": phase[0],
                                "inputSchema": {"type": "object"},
                                "_meta": {"connector_id": "fixture"},
                            }
                        ]
                    }
                elif method == "resources/list":
                    resources.append(index)
                    result = {"resources": []}
                else:
                    assert method == "tools/call"
                    calls.append((index, packet["params"]["name"]))
                    result = {"content": [{"type": "text", "text": "fresh catalog result"}]}
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
                turn, step = request.items[-1].turn_id, new_step_id()
                current = self.steps - int(during_sampling)
                if current == 0:
                    await refresh()
                    call = (
                        ToolCall(new_tool_call_id(), "list_mcp_resources", {})
                        if not nested
                        else ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments="text(await tools.list_mcp_resources({}));",
                        )
                    )
                elif current == 1 and not nested:
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": f"{server} new"})
                elif current == (1 if nested else 2):
                    if nested:
                        call = ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments=f"text(await tools.mcp__{server}__new({{}}));",
                        )
                    else:
                        assert f"mcp__{server}::new" in {tool.name for tool in request.tools}
                        assert f"mcp__{server}::old" not in {tool.name for tool in request.tools}
                        call = ToolCall(new_tool_call_id(), f"mcp__{server}::new", {})
                else:
                    output = [item for item in request.items if isinstance(item, ToolResultItem)][
                        -1
                    ]
                    assert not output.is_error and "fresh catalog result" in output.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                execution_permissions=None,
                mcp_servers=(MCPServerSettings(server, "http", url="https://fixture.invalid"),),
                tool_mode="code_mode_only" if nested else "direct",
                tool_search_mode="disabled" if nested else "compatible",
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "refresh.db",
            home_path=tmp_path / "home",
        )

        async def refresh():
            phase[0] = "new"
            runtime.request_mcp_refresh()
            await runtime._mcp_manager.refresh_if_dirty()
            refreshed = await runtime.mcp_tool_catalog()
            assert {entry.definition["name"] for entry in refreshed} == {"new"}
            assert lists == [0, 1]
            assert len(clients) == 2

        try:
            assert {entry.definition["name"] for entry in await runtime.mcp_tool_catalog()} == {
                "old"
            }
            assert lists == [0]
            if not during_sampling:
                await refresh()
            events = [event async for event in runtime.stream("find and call the refreshed tool")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert calls == [(1, "new")]
            # Resource tools retain the captured Step lease; the next Step's
            # discovered MCP function binds the refreshed client instead.
            assert resources == ([0] if during_sampling else [])
        finally:
            await runtime.aclose()
        assert all(client.is_closed for client in clients)

    asyncio.run(scenario())
