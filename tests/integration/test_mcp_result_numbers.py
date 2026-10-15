"""Exact remote results through carriers, Runtime, JS Number and cold history."""

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
from corki.protocol.wire_numbers import WireNumber, dumps_wire, loads_number_values
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["direct", "code_mode", "code_mode_only"])
@pytest.mark.parametrize("carrier", ["json", "sse"])
@pytest.mark.parametrize(
    "token,js",
    [
        ("1e999", "Infinity"),
        ("1e-999", "0"),
        ("1.234567890123456789", "1.2345678901234567"),
        ("9007199254740993", "9007199254740992"),
    ],
)
def test_exact_result_survives_runtime_ledger_and_restart(
    tmp_path, monkeypatch, caplog, mode, carrier, token, js
):
    async def scenario():
        calls, requests = [], []
        canonical = WireNumber(token).token

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
                    body = (
                        '{"jsonrpc":"2.0","id":'
                        + str(packet["id"])
                        + ',"result":{"content":[],"structuredContent":{"n":'
                        + token
                        + '},"_meta":{"n":'
                        + token
                        + ',"label":"PRIVATE"}}}'
                    )
                    notification = (
                        '{"jsonrpc":"2.0","method":"notifications/message",'
                        '"params":{"level":"info","data":' + token + "}}"
                    )
                    return httpx.Response(
                        200,
                        headers={
                            "content-type": "application/json"
                            if carrier == "json"
                            else "text/event-stream"
                        },
                        content=body
                        if carrier == "json"
                        else "data: " + notification + "\n\ndata: " + body + "\n\n",
                    )
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
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
                            raw_arguments=(
                                "const r = await tools.mcp__docs__read({}); "
                                "text(typeof r.structuredContent.n); "
                                "text(String(r.structuredContent.n));"
                            ),
                        )
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    result = [item for item in request.items if isinstance(item, ToolResultItem)][
                        -1
                    ]
                    assert not result.is_error
                    if mode == "direct":
                        assert result.content.endswith('{"n":' + canonical + "}")
                    else:
                        assert "Script completed" in result.content
                        assert "number" in result.content and js in result.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        database = tmp_path / "numbers.db"
        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            tool_mode=mode,
            tool_search_mode="disabled",
            mcp_servers=(MCPServerSettings("docs", "http", url="https://fixture.invalid/mcp"),),
        )

        async def create(thread=None):
            return await LangGraphRuntime.acreate(
                settings=settings,
                database_path=database,
                registry=ToolRegistry(),
                model=Model(),
                thread_id=thread,
            )

        runtime = await create()
        thread = runtime.thread_id
        try:
            with caplog.at_level("INFO", logger="corki.mcp.inbound"):
                events = [event async for event in runtime.stream("read")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            if carrier == "sse":
                assert any(
                    record.name == "corki.mcp.inbound"
                    and record.getMessage().endswith(": " + canonical)
                    for record in caplog.records
                )
            with sqlite3.connect(database) as db:
                (encoded,) = db.execute(
                    "SELECT result_json FROM tool_executions WHERE tool_name='mcp__docs::read'"
                ).fetchone()
            saved = loads_number_values(encoded)
            assert not saved["is_error"]
            assert (
                dumps_wire(saved["code_mode_output"]["value"]["structuredContent"]["n"])
                == canonical
            )
            event = loads_number_values(saved["mcp_result_json"])
            assert dumps_wire(event["_meta"]["n"]) == canonical
        finally:
            await runtime.aclose()
        runtime = await create(thread)
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
