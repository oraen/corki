import asyncio
import json
import sqlite3
from dataclasses import replace

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


@pytest.mark.parametrize("old,latest", [(1, 80), (80, 1), (None, 80), (80, None)])
@pytest.mark.parametrize("nested", [False, True])
def test_mcp_output_budget_comes_from_admitted_connection_and_survives_reopen(
    tmp_path, monkeypatch, old, latest, nested
):
    async def scenario():
        calls, requests, clients = [], [], []
        original = "X" * 10000

        def factory(settings):
            generation = len(clients)
            clients.append(settings)

            def handler(request):
                if request.method == "DELETE":
                    return httpx.Response(204)
                packet = json.loads(request.content)
                method = packet["method"]
                if method == "notifications/initialized":
                    return httpx.Response(202)
                if method == "initialize":
                    value = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}}}
                elif method == "tools/list":
                    value = {
                        "tools": [
                            {
                                "name": "read",
                                "inputSchema": {"type": "object"},
                                "_meta": {"output_token_limit": 100000},
                            }
                        ]
                    }
                else:
                    assert method == "tools/call"
                    calls.append(generation)
                    value = {
                        "content": [{"type": "text", "text": "ignored"}],
                        "structuredContent": {"value": original},
                        "_meta": {"secret": "PRIVATE", "fallback_token_limit_override": 100000},
                    }
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": packet["id"], "result": value}
                )

            return HttpMCPClient(settings, transport=httpx.MockTransport(handler))

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        server = MCPServerSettings(
            "docs",
            "http",
            url="https://fixture.invalid/mcp",
            tool_output_token_limits=() if old is None else (("read", old),),
        )
        current = replace(
            server, tool_output_token_limits=() if latest is None else (("read", latest),)
        )
        expected = ((latest if latest is not None else 10) * 12 + 9) // 10

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    runtime.request_mcp_refresh((current,))
                    call = (
                        ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            raw_arguments=(
                                "const r = await tools.mcp__docs__read({}); "
                                "text([r.structuredContent.value.length, "
                                "r.content[0].text, '_meta' in r]);"
                            ),
                            input_kind="freeform",
                        )
                        if nested
                        else ToolCall(new_tool_call_id(), "mcp__docs__read", {})
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    if nested:
                        # The outer exec history has its own tiny model cap.
                        assert "PRIVATE" not in result.content
                    else:
                        assert result.fallback_token_limit_override == expected
                        assert "ignored" not in result.content and "PRIVATE" not in result.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            tool_search_mode="disabled",
            tool_output_token_limit=10,
            mcp_servers=(server,),
            tool_mode="code_mode" if nested else "direct",
        )
        database = tmp_path / "session.db"

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
            events = [e async for e in runtime.stream("read")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert calls == [1]
            with sqlite3.connect(database) as connection:
                (encoded,) = connection.execute(
                    "SELECT result_json FROM tool_executions WHERE tool_name='mcp__docs__read'"
                ).fetchone()
                ledger = json.loads(encoded)
                assert ledger["fallback_token_limit_override"] == expected
                assert ledger["code_mode_output"]["value"] == {
                    "content": [{"type": "text", "text": "ignored"}],
                    "structuredContent": {"value": original},
                }
                assert (
                    "truncated" in ledger["content"] and len(ledger["content"]) < expected * 4 + 100
                )
                if nested:
                    (cell,) = connection.execute(
                        "SELECT result_json FROM tool_executions WHERE tool_name='exec'"
                    ).fetchone()
                    assert "10000" in cell and "ignored" in cell and "false" in cell
        finally:
            await runtime.aclose()
        cold = create(thread)
        try:
            assert isinstance([e async for e in cold.stream("continue")][-1], TurnCompleted)
            assert calls == [1] and len(requests) == 3
            with sqlite3.connect(database) as connection:
                assert (
                    connection.execute(
                        "SELECT result_json FROM tool_executions WHERE tool_name='mcp__docs__read'"
                    ).fetchone()[0]
                    == encoded
                )
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["protocol", "timeout", "http"])
def test_remote_mcp_errors_resolve_to_code_mode_error_values(tmp_path, failure):
    from corki.mcp.client import MCPProtocolError
    from corki.mcp.tools import MCPTool

    async def scenario():
        calls, requests = [], []

        class Client:
            async def call_tool(self, name, arguments):
                calls.append(name)
                if failure == "protocol":
                    raise MCPProtocolError("remote rejected")
                if failure == "timeout":
                    raise TimeoutError("remote timed out")
                raise httpx.ReadTimeout("remote timed out")

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(
                                    new_tool_call_id(),
                                    "exec",
                                    None,
                                    raw_arguments=(
                                        "const r = await tools.mcp__docs__read({}); "
                                        "text(r.isError); text(r.content[0].text);"
                                    ),
                                    input_kind="freeform",
                                ),
                                turn,
                                step,
                            ),
                        )
                    )
                else:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert "Script completed" in result.content and "true" in result.content
                    if failure != "protocol":
                        assert "execution outcome may be unknown" in result.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(MCPTool("docs", {"name": "read"}, Client()))
        database = tmp_path / "errors.db"
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, tool_mode="code_mode"
            ),
            database_path=database,
            registry=registry,
            model=Model(),
        )
        try:
            events = [e async for e in runtime.stream("read")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert calls == ["read"] and len(requests) == 2
            with sqlite3.connect(database) as db:
                (value,) = db.execute(
                    "SELECT result_json FROM tool_executions WHERE tool_name='mcp__docs__read'"
                ).fetchone()
                result = json.loads(value)
                assert result["is_error"] and not result.get("dispatch_error", False)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
