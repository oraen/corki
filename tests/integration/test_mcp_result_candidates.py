"""Raw candidate selection reaches model observations and durable recovery."""

import asyncio
import json
import sqlite3

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


@pytest.mark.parametrize("mode", ["direct", "code_mode", "code_mode_only"])
@pytest.mark.parametrize("carrier", ["json", "sse"])
@pytest.mark.parametrize(
    "earlier", ["tools", "messages", "task", "task_input", "completion_sequence", "tool_sequence"]
)
@pytest.mark.parametrize("valid", [False, True])
def test_raw_candidate_outcome_survives_runtime_and_restart(
    tmp_path, monkeypatch, mode, carrier, earlier, valid
):
    async def scenario():
        calls, requests = [], []

        async def unexpected_elicitation(*args, **kwargs):
            raise AssertionError("Classifying a task must not execute its input requests")

        if earlier == "task_input":
            monkeypatch.setattr(
                "corki.mcp.elicitation.ElicitationRouter.request", unexpected_elicitation
            )

        def factory(settings):
            def respond(request):
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
                    result = {"tools": [{"name": "read", "inputSchema": {"type": "object"}}]}
                else:
                    assert method == "tools/call"
                    calls.append(packet)
                    result = {
                        "content": [{"type": "text", "text": "selected-tool-result"}],
                        "private": "PRIVATE",
                    }
                    if earlier in ("tools", "messages"):
                        result[earlier] = [] if valid else [{}]
                    elif earlier == "completion_sequence":
                        result["completion"] = [[], None, None] if valid else [[], None]
                    elif earlier == "tool_sequence":
                        tool = ["n", None, None, {}, None, None, None, None]
                        result["tools"] = [tool if valid else tool[:-1]]
                    else:
                        result.update(taskId="t", createdAt="x", lastUpdatedAt="y")
                        if earlier == "task":
                            result["status"] = "working" if valid else "completed"
                        else:
                            result.update(
                                status="input_required",
                                inputRequests={
                                    "r": {
                                        "method": "elicitation/create",
                                        "params": {
                                            "mode": "unknown",
                                            "message": "input",
                                            **(
                                                {
                                                    "requestedSchema": {
                                                        "type": "object",
                                                        "properties": {},
                                                    }
                                                }
                                                if valid
                                                else {}
                                            ),
                                        },
                                    }
                                },
                            )
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

            return HttpMCPClient(settings, transport=httpx.MockTransport(respond))

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)

        class Model:
            async def stream(self, request):
                requests.append(request)
                assert "PRIVATE" not in repr(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    call = (
                        ToolCall(new_tool_call_id(), "mcp__docs::read", {})
                        if mode == "direct"
                        else ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments="const r = await tools.mcp__docs__read({}); text(r);",
                        )
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    if valid:
                        assert "MCPProtocolError" in result.content
                        assert "selected-tool-result" not in result.content
                    else:
                        assert "selected-tool-result" in result.content
                        assert "MCPProtocolError" not in result.content
                    if mode == "direct":
                        assert result.is_error is valid
                    else:
                        assert "Script completed" in result.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        database = tmp_path / "candidates.db"
        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            tool_mode=mode,
            tool_search_mode="disabled",
            mcp_servers=(MCPServerSettings("docs", "http", url="https://fixture.invalid/mcp"),),
        )

        def create(thread=None):
            return LangGraphRuntime.create(
                settings=settings,
                database_path=database,
                registry=ToolRegistry(),
                model=Model(),
                thread_id=thread,
            )

        runtime = create()
        thread = runtime.thread_id
        try:
            events = [event async for event in runtime.stream("read")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            with sqlite3.connect(database) as db:
                (encoded,) = db.execute(
                    "SELECT result_json FROM tool_executions WHERE tool_name='mcp__docs::read'"
                ).fetchone()
            saved = json.loads(encoded)
            assert saved["is_error"] is valid
            assert not saved.get("dispatch_error", False)
            assert saved["code_mode_output"]["value"].get("isError", False) is valid
        finally:
            await runtime.aclose()
        runtime = create(thread)
        try:
            events = [event async for event in runtime.stream("continue")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(calls) == 1 and len(requests) == 3
            with sqlite3.connect(database) as db:
                assert (
                    db.execute(
                        "SELECT result_json FROM tool_executions WHERE tool_name='mcp__docs::read'"
                    ).fetchone()[0]
                    == encoded
                )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
