"""Ordinary reconnect during review retains the exact admitted client lease."""

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


@pytest.mark.parametrize("mode", ["compatible", "native", "code_mode"])
@pytest.mark.parametrize("refresh", [False, True])
@pytest.mark.parametrize("action", ["accept", "decline", "cancel"])
def test_review_wait_allows_refresh_without_rebinding_admitted_execution(
    tmp_path, monkeypatch, mode, refresh, action
):
    async def scenario():
        clients, calls, prompts, refreshed, observations = [], [], [], [], []
        nested = mode == "code_mode"

        def factory(settings):
            index = len(clients)

            async def respond(request):
                packet = json.loads(request.content)
                if packet["method"] == "notifications/initialized":
                    return httpx.Response(202)
                if packet["method"] == "initialize":
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "fixture", "version": "1"},
                    }
                elif packet["method"] == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": "write",
                                "description": "Review lock fixture",
                                "inputSchema": {"type": "object"},
                                "annotations": {"readOnlyHint": False},
                                "_meta": {"connector_id": "mail"},
                            }
                        ]
                    }
                else:
                    assert packet["method"] == "tools/call"
                    calls.append((index, packet["params"]))
                    result = {"content": [{"type": "text", "text": "business result"}]}
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
                if self.steps == 1 and not nested:
                    call = ToolCall(
                        new_tool_call_id(), "tool_search", {"query": "Review lock fixture"}
                    )
                elif self.steps == (1 if nested else 2):
                    if nested:
                        call = ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments="const t=ALL_TOOLS.find("
                            't=>t.description.includes("Review lock fixture"));'
                            "text(await tools[t.name]({}));",
                        )
                    else:
                        found = [i for i in request.items if getattr(i, "discovered_tools", ())][-1]
                        call = ToolCall(new_tool_call_id(), found.discovered_tools[0].name, {})
                else:
                    observations.append(
                        [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    )
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
                mcp_servers=(
                    MCPServerSettings("codex_apps", "http", url="https://fixture.invalid"),
                ),
                mcp_approval_policy="on-request",
                api_mode="responses",
                model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
                tool_search_mode="disabled" if nested else mode,
                tool_mode="code_mode_only" if nested else "direct",
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "review.db",
            home_path=tmp_path / "home",
        )

        async def host(request):
            prompts.append(request)
            if refresh:
                try:
                    runtime.request_mcp_refresh()
                    await asyncio.wait_for(runtime._mcp_manager.refresh_if_dirty(), 0.5)
                except TimeoutError:
                    refreshed.append(False)
                else:
                    refreshed.append(True)
            runtime.respond_mcp_elicitation(
                request.server_name, request.request_id, action, content={"remember": True}
            )

        runtime.set_mcp_elicitation_handler(host)
        try:
            events = [event async for event in runtime.stream("write after review")]
        finally:
            await runtime.aclose()
        assert all(client.is_closed for client in clients)
        assert isinstance(events[-1], TurnCompleted), events[-1]
        assert (refreshed, len(prompts), len(calls)) == (
            [True] if refresh else [],
            1,
            int(action == "accept"),
        )
        assert observations[0].is_error is (action != "accept" and not nested)
        if action != "accept":
            assert f"user {'cancelled' if action == 'cancel' else 'rejected'} MCP tool call" in (
                observations[0].content
            )
        if action != "accept":
            assert not runtime._mcp_manager._approvals._session, (
                "stale or rejected approval must not persist a grant"
            )
        else:
            assert calls[0][0] == 0, "accepted call must retain its original client"
            assert "business result" in observations[0].content
            assert runtime._mcp_manager._approvals._session == {("codex_apps", None, None, "write")}
        assert not runtime._mcp_manager.elicitations._pending
        assert len(clients) == (2 if refresh else 1)

    asyncio.run(scenario())
