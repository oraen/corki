"""Typed numeric projection reaches actual tool observations and durable history."""

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
from corki.protocol.wire_numbers import dumps_wire, loads_number_values
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["direct", "code_mode", "code_mode_only"])
@pytest.mark.parametrize("carrier", ["json", "sse"])
@pytest.mark.parametrize(
    "token,expected",
    [
        ("16777217", "16777216.0"),
        ("9223372586610589697", "9.223373e+18"),
        ("10000000000000", "1e+13"),
        ("0.5", None),
    ],
)
def test_priority_rounding_survives_runtime_and_cold_history(
    tmp_path, monkeypatch, mode, carrier, token, expected
):
    async def scenario():
        calls, requests = [], []

        def factory(settings):
            def respond(request):
                packet = json.loads(request.content)
                method = packet["method"]
                if method == "notifications/initialized":
                    return httpx.Response(202)
                if method == "initialize":
                    result = (
                        '{"protocolVersion":"2025-06-18","capabilities":{},'
                        '"serverInfo":{"name":"fixture","version":"1"}}'
                    )
                elif method == "tools/list":
                    result = '{"tools":[{"name":"read","inputSchema":{"type":"object"}}]}'
                else:
                    assert method == "tools/call"
                    calls.append(packet)
                    result = (
                        '{"content":[{"type":"resource_link","uri":"urn:x","name":"numeric",'
                        '"annotations":[null,' + token + ',null]}],"_meta":{"secret":"PRIVATE"}}'
                    )
                body = '{"jsonrpc":"2.0","id":' + str(packet["id"]) + ',"result":' + result + "}"
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
                    return
                result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                assert ("MCPProtocolError" in result.content) is (expected is None)
                if mode == "direct":
                    assert result.is_error is (expected is None)
                    if expected is not None:
                        # Resource links become model-visible JSON text. Native
                        # spelling must survive, not just Python numeric equality.
                        assert '"priority":' + expected in result.content.replace(" ", "")
                elif expected is not None:
                    nested = json.loads(result.content_items[-1].text)
                    # JS Number serializes again inside Code Mode; test the value
                    # here, and the exact pre-JS token independently in the ledger.
                    assert nested["content"][0]["annotations"]["priority"] == float(expected)
                yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        database = tmp_path / "numeric.db"
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
            saved = loads_number_values(encoded)
            assert saved["is_error"] is (expected is None)
            assert not saved.get("dispatch_error", False)
            value = saved["code_mode_output"]["value"]
            if expected is None:
                assert value["isError"] is True
            else:
                assert dumps_wire(value) == (
                    '{"content":[{"type":"resource_link","uri":"urn:x","name":"numeric",'
                    '"annotations":{"priority":' + expected + "}}]}"
                )
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
