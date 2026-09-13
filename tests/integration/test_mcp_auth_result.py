import asyncio
import json
import sqlite3

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.events import ToolCallCompleted, TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["native", "compatible", "code_mode"])
@pytest.mark.parametrize("outcome", ["auth", "success", "transport"])
def test_host_result_events_ledger_private_projection_and_cold_no_replay(
    tmp_path, monkeypatch, mode, outcome
):
    async def scenario():
        calls, clients, requests = [], [], []
        challenge = 'Basic realm="PRIVATE, login", Bearer error="invalid_token"'
        expected = {
            "content": [
                {
                    "type": "text",
                    "text": "Authentication required" if outcome == "auth" else "public",
                }
            ],
            "isError": outcome == "auth",
            "_meta": {"mcp/www_authenticate": [challenge]}
            if outcome == "auth"
            else {"private": "PRIVATE"},
        }

        def factory(settings):
            def handle(request):
                message = json.loads(request.content)
                if "id" not in message:
                    return httpx.Response(202)
                if message["method"] == "initialize":
                    result = {
                        "capabilities": {},
                        "serverInfo": {"name": "fixture", "version": "1"},
                        "protocolVersion": "2025-06-18",
                    }
                elif message["method"] == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": "read",
                                "description": "needle",
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    }
                else:
                    assert message["method"] == "tools/call"
                    calls.append(message)
                    if outcome == "auth":
                        return httpx.Response(
                            401,
                            headers=[
                                ("www-authenticate", 'Basic realm="PRIVATE, login"'),
                                ("www-authenticate", 'Bearer error="invalid_token"'),
                            ],
                        )
                    if outcome == "transport":
                        raise httpx.ReadError("connection failed")
                    result = expected
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": message["id"], "result": result}
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(handle))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        name, nested = "mcp__docs::read", mode == "code_mode"

        class Model:
            count = 0

            async def stream(self, request):
                self.count += 1
                requests.append(request)
                assert "PRIVATE" not in repr(request.items)
                turn, step = request.items[-1].turn_id, new_step_id()
                if not nested and self.count == 1:
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "needle"})
                elif self.count == (1 if nested else 2):
                    call = (
                        ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments=(
                                "const r = await tools.mcp__docs__read({}); "
                                "text(r); text('_meta' in r);"
                            ),
                        )
                        if nested
                        else ToolCall(new_tool_call_id(), name, {})
                    )
                else:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    if nested:
                        assert "false" in result.content
                    else:
                        assert result.is_error == (outcome != "success")
                    assert ("Authentication required" in result.content) == (outcome == "auth")
                    yield ModelCompleted((AssistantMessageItem("handled", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            api_mode="responses",
            tool_search_mode="disabled" if nested else mode,
            tool_mode="code_mode" if nested else "direct",
            mcp_servers=(MCPServerSettings("docs", "http", url="https://fixture.invalid"),),
        )
        database = tmp_path / "history.db"

        def create(model, thread=None):
            return LangGraphRuntime.create(
                settings=settings,
                model=model,
                registry=ToolRegistry(),
                database_path=database,
                home_path=tmp_path / "home",
                thread_id=thread,
            )

        runtime = create(Model())
        try:
            events = [e async for e in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            completions = [
                e for e in events if isinstance(e, ToolCallCompleted) and e.tool_name == name
            ]
            assert len(completions) == len(calls) == 1
            event = completions[0]
            assert event.is_error == (outcome != "success")
            if outcome == "transport":
                assert (
                    event.mcp_result_json is None
                    and event.mcp_error == "ReadError: connection failed"
                )
            else:
                assert json.loads(event.mcp_result_json) == expected and event.mcp_error is None
            raw = await runtime._repository.load_items(runtime._thread_id)
            assert "PRIVATE" not in repr(raw)
            with sqlite3.connect(database) as db:
                encoded = db.execute(
                    "SELECT result_json FROM tool_executions WHERE tool_name=?", (name,)
                ).fetchone()[0]
            saved = json.loads(encoded)
            assert saved.get("mcp_result_json") == event.mcp_result_json
            assert saved.get("mcp_error") == event.mcp_error
            assert "_meta" not in saved["code_mode_output"]["value"]
        finally:
            await runtime.aclose()

        class Cold:
            async def stream(self, request):
                assert "PRIVATE" not in repr(request.items) and len(calls) == 1
                yield ModelCompleted(
                    (AssistantMessageItem("cold", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        cold = create(Cold(), runtime._thread_id)
        try:
            events = [e async for e in cold.stream("continue")]
            assert isinstance(events[-1], TurnCompleted)
            assert not any(isinstance(e, ToolCallCompleted) for e in events)
            after = await cold._repository.load_items(cold._thread_id)
            assert after[: len(raw)] == raw and len(calls) == 1
            with sqlite3.connect(database) as db:
                assert (
                    db.execute(
                        "SELECT result_json FROM tool_executions WHERE tool_name=?", (name,)
                    ).fetchone()[0]
                    == encoded
                )
        finally:
            await cold.aclose()
        assert all(c.is_closed for c in clients)

    asyncio.run(scenario())
