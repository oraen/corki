"""Real resource RPCs feed the model and survive cold history without replay."""

import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.mcp.tool_catalog_cache import MCPToolCatalogCache
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["direct", "code_mode"])
@pytest.mark.parametrize("carrier", ["json", "sse"])
@pytest.mark.parametrize("variant", ["aggregate", "pages", "invalid", "timeout"])
def test_resource_inventory_read_and_cold_history(tmp_path, monkeypatch, mode, carrier, variant):
    async def scenario():
        clients, requests, operations = [], [], []
        bad_started, healthy_started = asyncio.Event(), asyncio.Event()
        cache = MCPToolCatalogCache()

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
                        "serverInfo": {"name": "fixture", "version": "1"},
                    }
                elif method == "tools/list":
                    result = {"tools": []}
                elif method == "resources/list":
                    params = packet["params"]
                    operations.append((server.name, method, params.get("cursor")))
                    if server.name == "bad":
                        bad_started.set()
                        await healthy_started.wait()
                        if variant == "timeout":
                            await asyncio.Event().wait()
                        if variant == "invalid":
                            result = {
                                "resources": [{"uri": "private:partial", "name": "partial"}, 7]
                            }
                        else:
                            return httpx.Response(
                                200,
                                json={
                                    "jsonrpc": "2.0",
                                    "id": packet["id"],
                                    "error": {"code": -32603, "message": "fixture failure"},
                                },
                            )
                    else:
                        healthy_started.set()
                        if variant != "pages":
                            await bad_started.wait()
                        second = "cursor" in params
                        if second:
                            assert (
                                params["cursor"] == "next"
                                if variant == "pages"
                                else params["cursor"] == ""
                            )
                        result = {
                            "resources": [
                                {
                                    "uri": "fixture:second" if second else "fixture:first",
                                    "name": "second" if second else "first",
                                }
                            ]
                        }
                        if not second:
                            result["nextCursor"] = "next" if variant == "pages" else ""
                else:
                    assert method == "resources/read" and server.name == "healthy"
                    assert packet["params"]["uri"] == "fixture:second"
                    operations.append((server.name, method, None))
                    result = {"contents": [{"uri": "fixture:second", "text": "resource body"}]}
                body = json.dumps({"jsonrpc": "2.0", "id": packet["id"], "result": result})
                return httpx.Response(
                    200,
                    headers={
                        "content-type": "application/json"
                        if carrier == "json"
                        else "text/event-stream"
                    },
                    content=body if carrier == "json" else "data: " + body + "\n\n",
                )

            client = HttpMCPClient(server, transport=httpx.MockTransport(respond))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)

        class Model:
            async def stream(self, request):
                requests.append(request)
                step = len(requests)
                turn = request.items[-1].turn_id
                if step == 1:
                    name, args = (
                        "list_mcp_resources",
                        {"server": "  healthy  "} if variant == "pages" else {},
                    )
                elif variant == "pages" and step == 2:
                    output = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert '"nextCursor":"next"' in output.content
                    assert '"name":"second"' not in output.content
                    name, args = "list_mcp_resources", {"server": "healthy", "cursor": " next "}
                elif step == (3 if variant == "pages" else 2):
                    output = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert '"server":"healthy"' in output.content
                    assert '"name":"second"' in output.content
                    assert "private:partial" not in output.content
                    assert not output.is_error
                    if variant != "pages":
                        assert '"name":"first"' in output.content
                        assert "nextCursor" not in output.content
                    name, args = (
                        "read_mcp_resource",
                        {"server": " healthy ", "uri": " fixture:second "},
                    )
                else:
                    output = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert "resource body" in output.content
                    assert '"server":"healthy"' in output.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))
                    return
                call = (
                    ToolCall(new_tool_call_id(), name, args)
                    if mode == "direct"
                    else ToolCall(
                        new_tool_call_id(),
                        "exec",
                        None,
                        input_kind="freeform",
                        raw_arguments=f"text(await tools.{name}({json.dumps(args)}));",
                    )
                )
                yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))

            async def aclose(self):
                pass

        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            tool_search_mode="disabled",
            tool_mode=mode,
            mcp_servers=tuple(
                MCPServerSettings(
                    name,
                    "http",
                    url=f"https://{name}.invalid",
                    timeout_seconds=0.15 if name == "bad" else 1,
                )
                for name in ("bad", "healthy")
            ),
        )

        def create(thread=None):
            return LangGraphRuntime.create(
                settings=settings,
                model=Model(),
                registry=ToolRegistry(),
                database_path=tmp_path / "resources.db",
                home_path=tmp_path / "home",
                thread_id=thread,
                mcp_tool_catalog_cache=cache,
            )

        runtime = create()
        thread = runtime.thread_id
        try:
            events = [event async for event in runtime.stream("read resource")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            history = await runtime._repository.load_items(thread)
            first_operations = list(operations)
            assert [o for o in operations if o[0] == "healthy"] == [
                ("healthy", "resources/list", None),
                ("healthy", "resources/list", "next" if variant == "pages" else ""),
                ("healthy", "resources/read", None),
            ]
        finally:
            await runtime.aclose()
        runtime = create(thread)
        try:
            events = [event async for event in runtime.stream("continue")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert operations == first_operations
            restored = await runtime._repository.load_items(thread)
            assert restored[: len(history)] == history
        finally:
            await runtime.aclose()
        assert all(c.is_closed for c in clients)

    asyncio.run(scenario())
