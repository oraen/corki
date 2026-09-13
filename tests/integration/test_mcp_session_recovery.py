import asyncio
import json
import sqlite3
import time

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.http_client import OwnedHTTPClient
from corki.mcp.client import HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.events import ToolCallCompleted, TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mutation", ["updated", "deleted", "corrupt", "transport_error"])
def test_oauth_session_404_preserves_live_auth_but_cold_client_reloads(
    tmp_path, monkeypatch, mutation
):
    async def scenario():
        home = tmp_path / "home"
        home.mkdir()
        credentials = home / ".credentials.json"
        entry = {
            "server_name": "docs",
            "server_url": "https://fixture.invalid",
            "client_id": "fixture-client",
            "access_token": "initial",
            "issuer": "https://fixture.invalid",
            "refresh_token": "never-send-refresh",
            "expires_at": int(time.time() * 1000) + 3_600_000,
        }
        credentials.write_text(json.dumps({"entry": entry}))
        phase = "live"
        calls, metadata, handshakes, carriers = [], [], [], []
        before_failure = None

        def respond(request):
            nonlocal before_failure
            assert request.url.host == "fixture.invalid"
            assert b"never-send-refresh" not in request.content
            if "/.well-known/" in request.url.path:
                metadata.append((phase, request.url.path))
                assert "authorization" not in request.headers
                if "oauth-protected-resource" in request.url.path:
                    data = {
                        "resource": "https://fixture.invalid",
                        "authorization_servers": ["https://fixture.invalid"],
                    }
                else:
                    data = {
                        "issuer": "https://fixture.invalid",
                        "authorization_endpoint": "https://fixture.invalid/a",
                        "token_endpoint": "https://fixture.invalid/t",
                    }
                return httpx.Response(200, json=data)
            if request.method == "GET":
                return httpx.Response(405)
            if request.method == "DELETE":
                return httpx.Response(204)
            packet = json.loads(request.content)
            authorization = request.headers.get("authorization")
            expected = "Bearer initial" if phase == "live" else "Bearer rotated"
            if authorization != expected:
                return httpx.Response(401)
            if "id" not in packet:
                return httpx.Response(202)
            headers = {}
            if packet["method"] == "initialize":
                handshakes.append((phase, authorization))
                headers["mcp-session-id"] = str(len(handshakes))
                result = {
                    "capabilities": {},
                    "serverInfo": {"name": "fixture", "version": "1"},
                    "protocolVersion": "2025-06-18",
                }
            elif packet["method"] == "tools/list":
                result = {"tools": [{"name": "read", "inputSchema": {"type": "object"}}]}
            else:
                assert packet["method"] == "tools/call"
                calls.append((authorization, request.headers.get("mcp-session-id")))
                if len(calls) == 1:
                    before_failure = len(metadata)
                    if mutation == "deleted":
                        credentials.unlink()
                    elif mutation == "corrupt":
                        credentials.write_text("{invalid")
                    else:
                        credentials.write_text(
                            json.dumps({"entry": {**entry, "access_token": "rotated"}})
                        )
                    if mutation == "transport_error":
                        raise httpx.ReadError("unknown tool outcome", request=request)
                    return httpx.Response(404)
                result = {"content": [{"type": "text", "text": "recovered"}]}
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}, headers=headers
            )

        class Carrier(httpx.MockTransport):
            def __init__(self):
                super().__init__(respond)
                self.closed = False
                carriers.append(self)

            async def aclose(self):
                self.closed = True

        monkeypatch.setattr(httpx.AsyncClient, "_init_transport", lambda *a, **kw: Carrier())
        monkeypatch.setattr(OwnedHTTPClient, "_init_transport", lambda *a, **kw: Carrier())
        monkeypatch.setenv("NO_PROXY", "*")

        class Model:
            count = 0

            async def stream(self, request):
                self.count += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if phase == "cold":
                    assert any(t.name == "mcp__docs::read" for t in request.tools) == (
                        mutation in {"updated", "transport_error"}
                    )
                elif self.count == 1:
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(new_tool_call_id(), "mcp__docs::read", {}), turn, step
                            ),
                        )
                    )
                    return
                else:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert result.is_error == (mutation == "transport_error")
                    assert ("recovered" in result.content) == (mutation != "transport_error")
                yield ModelCompleted((AssistantMessageItem("handled", turn, step),))

            async def aclose(self):
                pass

        settings = CorkiSettings(
            tmp_path,
            execution_permissions=None,
            skills_enabled=False,
            tool_search_mode="disabled",
            mcp_oauth_credentials_store="file",
            mcp_servers=(MCPServerSettings("docs", "http", url="https://fixture.invalid"),),
        )

        def create():
            return LangGraphRuntime.create(
                settings=settings,
                model=Model(),
                registry=ToolRegistry(),
                home_path=home,
                database_path=tmp_path / (phase + ".db"),
            )

        runtime = create()
        try:
            events = [e async for e in runtime.stream("read")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(calls) == (1 if mutation == "transport_error" else 2)
            assert len(metadata) == before_failure, "404 re-resolved OAuth metadata/source"
            assert [auth for _, auth in handshakes] == ["Bearer initial"] * len(calls)
            assert len([e for e in events if isinstance(e, ToolCallCompleted)]) == 1
        finally:
            await runtime.aclose()
        phase = "cold"
        cold = create()
        try:
            events = [e async for e in cold.stream("inspect")]
            assert isinstance(events[-1], TurnCompleted)
        finally:
            await cold.aclose()
        assert all(carrier.closed for carrier in carriers)

    asyncio.run(asyncio.wait_for(scenario(), 15))


@pytest.mark.parametrize("mode", ["native", "compatible", "code_mode"])
@pytest.mark.parametrize(
    "fault", ["session404", "double404", "disconnect", "timeout", "deadline_send", "deadline_body"]
)
def test_search_recover_observe_ledger_and_cold_history(tmp_path, monkeypatch, mode, fault):
    async def scenario():
        calls, clients, handshakes = [], [], []
        cleaned = []
        double404 = fault == "double404"
        failed = fault != "session404"
        deadline = fault.startswith("deadline_")
        expected_calls = 2 if fault in {"session404", "double404"} else 1

        class PendingBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                try:
                    yield b'{"jsonrpc":"2.0","result":'
                    await asyncio.Event().wait()
                finally:
                    cleaned.append("reader")

            async def aclose(self):
                cleaned.append("response")

        def factory(settings):
            async def handle(request):
                if request.method == "GET":
                    return httpx.Response(405)
                if request.method == "DELETE":
                    return httpx.Response(204)
                message = json.loads(request.content)
                if "id" not in message:
                    return httpx.Response(202)
                method = message["method"]
                headers = {}
                if method == "initialize":
                    handshakes.append(message)
                    headers["mcp-session-id"] = str(len(handshakes))
                    result = {
                        "capabilities": {},
                        "serverInfo": {"name": "fixture", "version": "1"},
                        "protocolVersion": "2025-06-18",
                    }
                elif method == "tools/list":
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
                    assert method == "tools/call"
                    calls.append((message, request.headers.get("mcp-session-id")))
                    if fault == "disconnect":
                        raise httpx.ReadError("unknown remote outcome", request=request)
                    if fault == "timeout":
                        raise httpx.ReadTimeout("unknown remote outcome", request=request)
                    if fault == "deadline_send":
                        try:
                            await asyncio.Event().wait()
                        finally:
                            cleaned.append("send")
                    if fault == "deadline_body":
                        return httpx.Response(
                            200,
                            headers={"content-type": "application/json"},
                            stream=PendingBody(),
                        )
                    if len(calls) <= (2 if double404 else 1):
                        return httpx.Response(404)
                    result = {"content": [{"type": "text", "text": "recovered value"}]}
                return httpx.Response(
                    200,
                    json={"jsonrpc": "2.0", "id": message["id"], "result": result},
                    headers=headers,
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
                            raw_arguments="text(await tools.mcp__docs__read({}));",
                        )
                        if nested
                        else ToolCall(new_tool_call_id(), name, {})
                    )
                else:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert ("404" in result.content) == double404
                    assert ("recovered value" in result.content) != failed
                    if not nested:
                        assert result.is_error == failed
                    if fault == "timeout" or deadline:
                        assert "execution outcome may be unknown" in result.content
                    if deadline:
                        assert cleaned == (
                            ["send"] if fault == "deadline_send" else ["reader", "response"]
                        )
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
            mcp_servers=(
                MCPServerSettings(
                    "docs",
                    "http",
                    url="https://fixture.invalid",
                    timeout_seconds=0.2 if deadline else 30,
                ),
            ),
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
            assert len(completions) == 1
            assert len(calls) == len(handshakes) == expected_calls
            assert [session for _, session in calls] == ["1", "2"][:expected_calls]
            before = calls[0][0]["params"]
            assert before["_meta"] == {"progressToken": 1}  # Initial catalog consumed0.
            if expected_calls == 2:
                after = calls[1][0]["params"]
                assert after["_meta"] == {"progressToken": 0}
                assert {**before, "_meta": after["_meta"]} == after
            event = completions[0]
            assert event.is_error == failed
            assert (event.mcp_error is not None) == failed
            assert (event.mcp_result_json is None) == failed
            raw = await runtime._repository.load_items(runtime._thread_id)
            with sqlite3.connect(database) as db:
                rows = db.execute(
                    "SELECT result_json FROM tool_executions WHERE tool_name=?", (name,)
                ).fetchall()
            assert len(rows) == 1
            encoded = rows[0][0]
        finally:
            await runtime.aclose()

        class Cold:
            async def stream(self, request):
                assert len(calls) == expected_calls
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
            assert after[: len(raw)] == raw and len(calls) == expected_calls
            with sqlite3.connect(database) as db:
                assert db.execute(
                    "SELECT result_json FROM tool_executions WHERE tool_name=?", (name,)
                ).fetchall() == [(encoded,)]
        finally:
            await cold.aclose()
        assert all(c.is_closed for c in clients)

    asyncio.run(asyncio.wait_for(scenario(), 15))
