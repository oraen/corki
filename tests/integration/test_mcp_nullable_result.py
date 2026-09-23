"""Nullable completed results survive HTTP, Runtime, nested calls and the ledger."""

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
@pytest.mark.parametrize(
    "field,value",
    [
        ("isError", False),
        ("isError", True),
        ("structuredContent", False),
        ("structuredContent", []),
    ],
)
def test_nullable_content_is_normalized_before_model_and_durable_result(
    tmp_path, monkeypatch, mode, field, value
):
    async def scenario():
        calls, requests = [], []
        raw = {
            "content": None,
            field: value,
            "_meta": {"secret": "PRIVATE"},
            "resultType": "complete",
        }

        def factory(settings):
            def handler(request):
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
                    result = {"tools": [{"name": "read", "inputSchema": {"type": "object"}}]}
                else:
                    assert packet["method"] == "tools/call"
                    calls.append(packet)
                    result = raw
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
                )

            return HttpMCPClient(settings, transport=httpx.MockTransport(handler))

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
                    assert "MCPProtocolError" not in result.content
                    if mode == "direct":
                        assert result.is_error == (field == "isError" and value is True)
                    else:
                        assert "Script completed" in result.content
                        assert '"content":[]' in result.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        database = tmp_path / "nullable.db"
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                execution_permissions=None,
                tool_mode=mode,
                tool_search_mode="disabled",
                mcp_servers=(MCPServerSettings("docs", "http", url="https://nullable.test/mcp"),),
            ),
            registry=ToolRegistry(),
            model=Model(),
            database_path=database,
        )
        try:
            events = [event async for event in runtime.stream("read")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(calls) == 1 and len(requests) == 2
            with sqlite3.connect(database) as db:
                (encoded,) = db.execute(
                    "SELECT result_json FROM tool_executions WHERE tool_name='mcp__docs::read'"
                ).fetchone()
            result = json.loads(encoded)
            assert result["code_mode_output"]["value"] == {"content": [], field: value}
            assert not result.get("dispatch_error", False)
            assert json.loads(result["mcp_result_json"])["_meta"] == {"secret": "PRIVATE"}
            assert raw["content"] is None
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
