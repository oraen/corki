"""Ordinary session consent and review cancellation follow real Runtime calls."""

import asyncio
import json
import sqlite3

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCancelled, TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["compatible", "native", "code_mode"])
@pytest.mark.parametrize("case", ["same", "changed_argument", "cancel_review"])
@pytest.mark.parametrize("server", ["docs", "codex_apps"])
def test_session_consent_and_cancel_preserve_call_authority(
    tmp_path, monkeypatch, mode, case, server
):
    async def scenario():
        clients, calls, prompts, outputs = [], [], [], []
        waiting, release = asyncio.Event(), asyncio.Event()
        nested = mode == "code_mode"
        repeat = case in ("same", "changed_argument")

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
                    }
                elif packet["method"] == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": "Gmail_Write",
                                "description": "Account policy fixture",
                                "inputSchema": {"type": "object"},
                                "annotations": {"readOnlyHint": False},
                                "_meta": {
                                    "connector_id": "mail",
                                    "connector_name": "Gmail",
                                    "_codex_apps": {"requires_explicit_link_id": True},
                                },
                            }
                        ]
                    }
                else:
                    assert packet["method"] == "tools/call"
                    calls.append(packet["params"])
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
                first = 1 if nested else 2
                if self.steps == 1 and not nested:
                    call = ToolCall(
                        new_tool_call_id(), "tool_search", {"query": "Account policy fixture"}
                    )
                elif self.steps == first or (repeat and self.steps == first + 1):
                    if self.steps > first:
                        outputs.append(
                            [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                        )
                    arguments = {
                        "link_id": "other"
                        if case == "changed_argument" and self.steps > first
                        else "account"
                    }
                    if nested:
                        call = ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments="const t=ALL_TOOLS.find("
                            't=>t.description.includes("Account policy fixture"));'
                            + "text(await tools[t.name]("
                            + json.dumps(arguments)
                            + "));",
                        )
                    else:
                        found = [i for i in request.items if getattr(i, "discovered_tools", ())][-1]
                        call = ToolCall(
                            new_tool_call_id(), found.discovered_tools[0].name, arguments
                        )
                else:
                    outputs.append([i for i in request.items if isinstance(i, ToolResultItem)][-1])
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                execution_permissions=None,
                mcp_servers=(MCPServerSettings(server, "http", url="https://fixture.invalid"),),
                mcp_approval_policy="on-request",
                api_mode="responses",
                model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
                tool_search_mode="disabled" if nested else mode,
                tool_mode="code_mode_only" if nested else "direct",
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "policy.db",
            home_path=tmp_path / "home",
        )

        async def host(request):
            prompts.append(request)
            waiting.set()
            if case == "cancel_review":
                await release.wait()
            runtime.respond_mcp_elicitation(
                request.server_name,
                request.request_id,
                "accept" if len(prompts) == 1 else "decline",
                content={"remember": True},
            )

        runtime.set_mcp_elicitation_handler(host)
        cancelled = []

        async def collect():
            events = []
            try:
                async for event in runtime.stream("write through the account tool"):
                    events.append(event)
            except asyncio.CancelledError:
                cancelled.append(True)
            return events

        task = asyncio.create_task(collect())
        try:
            if case == "cancel_review":
                await asyncio.wait_for(waiting.wait(), 5)
                await runtime.cancel_active()
            events = await asyncio.wait_for(task, 10)
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()
        assert all(client.is_closed for client in clients)
        with sqlite3.connect(tmp_path / "policy.db") as db:
            rows = db.execute(
                "SELECT status,result_json FROM tool_executions WHERE tool_name LIKE 'mcp__%'"
            ).fetchall()
        if case == "cancel_review":
            assert isinstance(events[-1], TurnCancelled) and cancelled == [True]
            assert not calls and not outputs and len(prompts) == 1
            assert rows[0][0] != "completed" and rows[0][1] is None
            assert not runtime._mcp_manager.elicitations._pending
            return
        assert isinstance(events[-1], TurnCompleted) and not cancelled, events[-1]
        assert (len(prompts), len(calls)) == (1, 2)
        assert len(rows) == 2 and all(row[0] == "completed" for row in rows)
        assert all(not json.loads(row[1])["is_error"] for row in rows)
        assert all(not json.loads(row[1]).get("dispatch_error", False) for row in rows)
        assert all("business result" in output.content for output in outputs)
        assert calls[0]["arguments"] == {"link_id": "account"}
        assert calls[1]["arguments"] == {
            "link_id": "other" if case == "changed_argument" else "account"
        }
        assert "connector_id" not in prompts[0].params.get("_meta", {})
        assert "link_id" not in prompts[0].params.get("_meta", {})

    asyncio.run(scenario())
