"""Independent MCP starts must not be serialized behind another server's handshake."""

import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import PROTOCOL_VERSION, HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import ToolCallId
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("action", ["complete", "cancel", "close"])
@pytest.mark.parametrize("first_delayed", [False, True])
def test_parallel_mcp_startup_is_owned_through_runtime(
    tmp_path, monkeypatch, required, action, first_delayed
):
    async def scenario():
        entered = {name: asyncio.Event() for name in ("a_first", "z_second")}
        clients, requests, calls, events = [], [], [], []

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    selected = [t for t in request.tools if t.name.endswith("::lookup")]
                    assert len(selected) == 2
                    yield ModelCompleted(
                        tuple(
                            ToolCallItem(ToolCall(ToolCallId(f"call-{n}"), t.name, {}), turn, step)
                            for n, t in enumerate(selected)
                        )
                    )
                else:
                    results = [item for item in request.items if isinstance(item, ToolResultItem)]
                    assert len(results) == 2 and all(not item.is_error for item in results)
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        def factory(settings):
            async def handle(request):
                if request.method == "GET":
                    return httpx.Response(405)
                if request.method == "DELETE":
                    return httpx.Response(204)
                message = json.loads(request.content)
                method = message["method"]
                if method == "notifications/initialized":
                    return httpx.Response(202)
                if method == "initialize":
                    result = {
                        "protocolVersion": PROTOCOL_VERSION,
                        "serverInfo": {"name": settings.name, "version": "1"},
                        "capabilities": {"tools": {}},
                    }
                elif method == "tools/list":
                    entered[settings.name].set()
                    if action == "complete":
                        # No wall-clock race: first cannot finish before second has begun.
                        await entered["z_second"].wait()
                    else:
                        await asyncio.Event().wait()
                    result = {"tools": [{"name": "lookup", "inputSchema": {"type": "object"}}]}
                elif method == "tools/call":
                    calls.append(settings.name)
                    result = {"content": [{"type": "text", "text": settings.name}]}
                else:
                    raise AssertionError(method)
                return httpx.Response(
                    200,
                    headers={"mcp-session-id": settings.name},
                    json={"jsonrpc": "2.0", "id": message["id"], "result": result},
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(handle))
            if first_delayed and settings.name == "a_first":
                prepare = client._prepare_oauth

                async def delayed_prepare():
                    await entered["z_second"].wait()
                    await prepare()

                client._prepare_oauth = delayed_prepare
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                execution_permissions=None,
                tool_search_mode="disabled",
                mcp_servers=tuple(
                    MCPServerSettings(name, "http", url=f"https://{name}.test", required=required)
                    for name in entered
                ),
            ),
            registry=ToolRegistry(),
            database_path=tmp_path / "sessions.db",
            model=Model(),
        )

        async def consume():
            async for event in runtime.stream("call both"):
                events.append(event)

        consumer = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(asyncio.gather(*(ready.wait() for ready in entered.values())), 1)
            if action == "complete":
                await asyncio.wait_for(consumer, 3)
                assert isinstance(events[-1], TurnCompleted)
                assert len(requests) == 2 and sorted(calls) == ["a_first", "z_second"]
            else:
                if action == "close":
                    await asyncio.wait_for(runtime.aclose(), 3)
                else:
                    consumer.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(consumer, 3)
                assert not requests and not calls
                # Optional startup publishes resource entry points before readiness;
                # required startup still has not published a generation at this point.
                assert runtime._mcp_manager.tool_names == (
                    ()
                    if required
                    else (
                        "list_mcp_resources",
                        "list_mcp_resource_templates",
                        "read_mcp_resource",
                    )
                )
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            await runtime.aclose()
        assert len(clients) == 2 and all(client._client.is_closed for client in clients)
        assert not [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and task.get_name().startswith("corki-mcp-start")
        ]

    asyncio.run(scenario())
