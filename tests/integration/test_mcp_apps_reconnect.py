"""Startup failure recovery reaches actual Runtime search, dispatch and observations."""

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


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("name", ["codex_apps", "ordinary"])
@pytest.mark.parametrize("call_failure", [False, True])
def test_failed_startup_recovers_only_after_explicit_host_refresh(
    tmp_path, monkeypatch, nested, name, call_failure
):
    async def scenario():
        clients, calls = [], []
        reconnect_started, release = asyncio.Event(), asyncio.Event()

        def factory(settings):
            attempt = len(clients)

            async def respond(request):
                packet = json.loads(request.content)
                method = packet["method"]
                if method == "notifications/initialized":
                    return httpx.Response(202)
                if method == "initialize":
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": name, "version": str(attempt)},
                    }
                elif method == "tools/list":
                    if attempt == 0:
                        return httpx.Response(
                            200,
                            json={
                                "jsonrpc": "2.0",
                                "id": packet["id"],
                                "error": {"code": -32000, "message": "initial startup failed"},
                            },
                        )
                    reconnect_started.set()
                    await release.wait()
                    result = {
                        "tools": [
                            {
                                "name": "needle",
                                "description": "recovered needle",
                                "_meta": {"connector_id": "fixture"},
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    }
                else:
                    assert method == "tools/call"
                    calls.append(attempt)
                    if call_failure:
                        return httpx.Response(
                            200,
                            json={
                                "jsonrpc": "2.0",
                                "id": packet["id"],
                                "error": {
                                    "code": -32000,
                                    "message": "fixture business call failed",
                                },
                            },
                        )
                    result = {"content": [{"type": "text", "text": "recovered result"}]}
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
                if self.steps == 1 and not nested:
                    call = ToolCall(
                        new_tool_call_id(), "tool_search", {"query": "recovered needle"}
                    )
                elif self.steps == (1 if nested else 2):
                    if nested:
                        call = ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments=f"text(await tools.mcp__{name}__needle({{}}));",
                        )
                    else:
                        assert f"mcp__{name}::needle" in {t.name for t in request.tools}
                        call = ToolCall(new_tool_call_id(), f"mcp__{name}::needle", {})
                else:
                    output = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert output.is_error is (call_failure and not nested)
                    if call_failure and nested:
                        # The cell successfully prints an admitted MCP error value;
                        # this is not a JavaScript host-dispatch exception.
                        assert '"isError":true' in output.content.replace(" ", "")
                    assert (
                        "fixture business call failed" if call_failure else "recovered result"
                    ) in output.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                execution_permissions=None,
                tool_mode="code_mode_only" if nested else "direct",
                tool_search_mode="disabled" if nested else "compatible",
                mcp_servers=(MCPServerSettings(name, "http", url="https://fixture.invalid"),),
                mcp_optional_startup_grace_ms=1,
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "reconnect.db",
            home_path=tmp_path / "home",
        )
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.start()
            for _ in range(3):
                assert await runtime.mcp_tool_catalog() == ()
            assert len(clients) == 1 and clients[0].is_closed
            assert not calls and not reconnect_started.is_set()
            runtime.request_mcp_refresh()
            release.set()
            await runtime._mcp_manager.start()
            assert reconnect_started.is_set()
            events = [event async for event in runtime.stream("recover catalog")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert calls == [1], "a failed business call must not be replayed"
            assert len(clients) == 2
        finally:
            release.set()
            await runtime.aclose()
        assert all(client.is_closed for client in clients)

    asyncio.run(scenario())
