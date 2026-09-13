"""Ordinary raw-call dedup binds visibility and approval without connector authority."""

import asyncio
import json
import sqlite3
from copy import deepcopy

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
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "case",
    [
        "shadow_last",
        "shadow_first",
        "annotation_last",
        "annotation_first",
        "missing",
        "null",
        "number",
        "blank",
        "valid",
        "legacy",
        "ordinary",
        "late_required",
        "late_optional",
    ],
)
def test_exact_binding_controls_visibility_approval_and_account(
    tmp_path, monkeypatch, mode, reverse, case
):
    async def scenario():
        clients, calls, prompts, outputs = [], [], [], []
        server = "ordinary" if case == "ordinary" else "codex_apps"
        base = {
            "name": "Gmail_Read",
            "description": "Exact call fixture",
            "inputSchema": {"type": "object"},
            "annotations": {"readOnlyHint": True},
            "_meta": {"connector_id": "a", "connector_name": "Gmail", "link_id": "default"},
        }
        definitions = [deepcopy(base)]
        arguments = {}
        if case.startswith(("shadow_", "annotation_")):
            definitions.append(deepcopy(base))
            definitions[1]["_meta"]["connector_id"] = "z"
            selected = definitions[1 if case.endswith("last") else 0]
            if case.startswith("shadow"):
                selected["_meta"]["ui"] = {"visibility": ["app"]}
            else:
                selected["annotations"]["readOnlyHint"] = False
        else:
            definitions[0]["_meta"]["_codex_apps"] = {
                "requires_explicit_link_id": case not in ("legacy", "late_required")
            }
            if case in ("null", "number", "blank", "valid", "legacy"):
                arguments["link_id"] = {
                    "null": None,
                    "number": 5,
                    "blank": " \u2003",
                    "valid": " selected ",
                    "legacy": "unrelated",
                }[case]
        if reverse:
            definitions.reverse()
        hidden = case.startswith("shadow_") and (
            (case.endswith("first") and not reverse) or (case.endswith("last") and reverse)
        )
        denied = case.startswith("annotation_") and (
            (case.endswith("first") and not reverse) or (case.endswith("last") and reverse)
        )

        def factory(settings):
            async def respond(request):
                assert str(request.url).rstrip("/") == "https://fixture.invalid"
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
                    result = {"tools": definitions}
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
                if self.steps == 1 and mode != "code_mode":
                    call = ToolCall(
                        new_tool_call_id(), "tool_search", {"query": "Exact call fixture"}
                    )
                elif self.steps == (1 if mode == "code_mode" else 2):
                    if case.startswith("late_"):
                        definitions[0]["_meta"]["_codex_apps"]["requires_explicit_link_id"] = (
                            case == "late_required"
                        )
                        runtime.request_mcp_refresh()
                        await runtime._mcp_manager.refresh_if_dirty()
                    if mode == "code_mode":
                        call = ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments="const t = ALL_TOOLS.find("
                            't => t.description.includes("Exact call fixture"));'
                            'if (!t) { text("not exposed"); } else { '
                            "text(await tools[t.name](" + json.dumps(arguments) + ")); }",
                        )
                    else:
                        search = [
                            i
                            for i in request.items
                            if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
                        ][-1]
                        if hidden:
                            assert not search.discovered_tools
                            assert f"mcp__{server}::Gmail_Read" not in {
                                t.name for t in request.tools
                            }
                            yield ModelCompleted((AssistantMessageItem("not exposed", turn, step),))
                            return
                        assert len(search.discovered_tools) == 1
                        assert search.discovered_tools[0].name in {t.name for t in request.tools}
                        call = ToolCall(
                            new_tool_call_id(), search.discovered_tools[0].name, arguments
                        )
                else:
                    outputs.append([i for i in request.items if isinstance(i, ToolResultItem)][-1])
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
                mcp_approval_policy="on-request",
                api_mode="responses",
                model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
                tool_search_mode="disabled" if mode == "code_mode" else mode,
                tool_mode="code_mode_only" if mode == "code_mode" else "direct",
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "metadata.db",
            home_path=tmp_path / "home",
        )

        async def host(request):
            prompts.append(request)
            runtime.respond_mcp_elicitation(request.server_name, request.request_id, "decline")

        runtime.set_mcp_elicitation_handler(host)
        try:
            events = [e async for e in runtime.stream("call the discovered tool")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(calls) == int(not hidden and not denied), case
            assert len(prompts) == int(denied)
            if calls:
                assert calls[0]["name"] == "Gmail_Read" and calls[0]["arguments"] == arguments
            with sqlite3.connect(tmp_path / "metadata.db") as db:
                rows = db.execute(
                    "SELECT status,result_json FROM tool_executions WHERE tool_name LIKE 'mcp__%'"
                ).fetchall()
            if hidden:
                assert rows == []
                if mode == "code_mode":
                    assert not outputs[0].is_error and "not exposed" in outputs[0].content
            else:
                output = outputs[0]
                assert output.is_error == (denied and mode != "code_mode")
                assert (
                    "user rejected MCP tool call" if denied else "business result"
                ) in output.content
                assert len(rows) == 1 and rows[0][0] == "completed"
                result = json.loads(rows[0][1])
                assert result["is_error"] == denied and not result.get("dispatch_error", False)
            if prompts:
                assert "connector_id" not in prompts[0].params.get("_meta", {})
                assert "link_id" not in prompts[0].params.get("_meta", {})
            assert len(clients) == (2 if case.startswith("late_") else 1)
        finally:
            await runtime.aclose()
        assert all(c.is_closed for c in clients)

    asyncio.run(scenario())
