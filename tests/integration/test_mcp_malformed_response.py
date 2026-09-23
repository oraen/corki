import asyncio
import json
import sqlite3

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import PROTOCOL_VERSION, HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize(
    "malformed",
    [
        "content-null",
        "content-object",
        "error-string",
        "error-int",
        "json",
        "utf8",
        "nan",
        "surrogate",
        "empty",
        "unknown-only",
        "all-null",
        "ack-only",
        "input-required",
        "future-result",
        "non-string-result",
        "meta-array",
        "block-null",
        "block-unknown",
        "block-text-missing",
        "block-image-missing-mime",
        "block-resource-missing-uri",
        "block-link-missing-name",
        "duplicate-content",
        "duplicate-error",
        "duplicate-type",
        "duplicate-resource-text",
    ],
)
def test_remote_malformed_response_is_error_value_without_replay(
    tmp_path, monkeypatch, nested, malformed
):
    async def scenario():
        calls, requests = [], []

        def factory(settings):
            def handler(request):
                packet = json.loads(request.content)
                method = packet["method"]
                if method == "notifications/initialized":
                    return httpx.Response(202)
                if method == "initialize":
                    result = {
                        "capabilities": {},
                        "serverInfo": {"name": "fixture", "version": "1"},
                        "protocolVersion": PROTOCOL_VERSION,
                    }
                elif method == "tools/list":
                    result = {"tools": [{"name": "read", "inputSchema": {"type": "object"}}]}
                else:
                    assert method == "tools/call"
                    calls.append(packet)
                    duplicate_bodies = {
                        "duplicate-content": '{"content":null,"content":[]}',
                        "duplicate-error": '{"content":[],"isError":true,"isError":false}',
                        "duplicate-type": (
                            '{"content":[{"type":"PRIVATE","type":"text","text":"ok"}]}'
                        ),
                        "duplicate-resource-text": (
                            '{"content":[{"type":"resource","resource":'
                            '{"uri":"x","text":"PRIVATE","text":"ok"}}]}'
                        ),
                    }
                    if malformed in duplicate_bodies:
                        return httpx.Response(
                            200,
                            headers={"content-type": "application/json"},
                            content=(
                                '{"jsonrpc":"2.0","id":'
                                + str(packet["id"])
                                + ',"result":'
                                + duplicate_bodies[malformed]
                                + "}"
                            ).encode(),
                        )
                    if malformed in {"json", "utf8"}:
                        return httpx.Response(
                            200, content=b"{PRIVATE" if malformed == "json" else b"\xff"
                        )
                    result = {
                        "content-null": {"content": None},
                        "content-object": {"content": {}},
                        "error-string": {"content": [], "isError": "false"},
                        "error-int": {"content": [], "isError": 1},
                        "nan": {"content": [], "structuredContent": float("nan")},
                        "surrogate": {"content": [], "structuredContent": "\ud800"},
                        "empty": {},
                        "unknown-only": {"unknown": "PRIVATE"},
                        "all-null": {
                            "content": None,
                            "structuredContent": None,
                            "isError": None,
                            "_meta": None,
                        },
                        "ack-only": {"resultType": "complete"},
                        "input-required": {
                            "resultType": "input_required",
                            "requestState": "PRIVATE",
                            "_meta": {},
                        },
                        "future-result": {"content": [], "resultType": "PRIVATE"},
                        "non-string-result": {"content": [], "resultType": 1},
                        "meta-array": {"content": [], "_meta": []},
                        "block-null": {"content": [None]},
                        "block-unknown": {"content": [{"type": "PRIVATE"}]},
                        "block-text-missing": {"content": [{"type": "text"}]},
                        "block-image-missing-mime": {
                            "content": [{"type": "image", "data": "AAAA"}]
                        },
                        "block-resource-missing-uri": {
                            "content": [{"type": "resource", "resource": {"text": "PRIVATE"}}]
                        },
                        "block-link-missing-name": {
                            "content": [{"type": "resource_link", "uri": "PRIVATE"}]
                        },
                    }[malformed]
                return httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    content=json.dumps(
                        {"jsonrpc": "2.0", "id": packet["id"], "result": result}
                    ).encode(),
                )

            return HttpMCPClient(settings, transport=httpx.MockTransport(handler))

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    call = (
                        ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments=(
                                "const r = await tools.mcp__docs__read({}); "
                                "text(r.isError); text(r.content[0].text);"
                            ),
                        )
                        if nested
                        else ToolCall(new_tool_call_id(), "mcp__docs::read", {})
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert "MCPProtocolError" in result.content
                    assert "PRIVATE" not in result.content
                    if nested:
                        assert "Script completed" in result.content and "true" in result.content
                    else:
                        assert result.is_error
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        database = tmp_path / "malformed.db"
        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            tool_search_mode="disabled",
            tool_mode="code_mode" if nested else "direct",
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
            events = [e async for e in runtime.stream("read")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            with sqlite3.connect(database) as db:
                (encoded,) = db.execute(
                    "SELECT result_json FROM tool_executions WHERE tool_name='mcp__docs::read'"
                ).fetchone()
                result = json.loads(encoded)
                assert result["is_error"] and not result.get("dispatch_error", False)
                assert result["code_mode_output"]["value"]["isError"] is True
        finally:
            await runtime.aclose()
        runtime = await create(thread)
        try:
            events = [e async for e in runtime.stream("continue")]
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
