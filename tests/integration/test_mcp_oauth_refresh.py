"""Runtime File OAuth refresh uses only the selected MCP and issuer endpoints."""

import asyncio
import json
import time
from urllib.parse import parse_qs

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.http_client import OwnedHTTPClient
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tool_names import compatible_tool_name
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("name", ["docs", "openai"])
@pytest.mark.parametrize("when", ["startup", "between_calls"])
@pytest.mark.parametrize("rejected", [False, True])
@pytest.mark.parametrize("slow_refresh", [False, True])
@pytest.mark.parametrize("model_transport", ["scripted", "responses"])
@pytest.mark.parametrize("deferred", [False, True])
def test_runtime_refresh_persists_before_rpc_and_cold_restart(
    tmp_path, monkeypatch, name, when, rejected, slow_refresh, model_transport, deferred
):
    async def scenario():
        base = "https://mcp.fixture.invalid"
        path = tmp_path / ".credentials.json"
        epoch = int(time.time() * 1000)
        entry = dict(
            server_name=name,
            server_url=base,
            client_id="client",
            access_token="mcp-access-old",
            refresh_token="rotate-once",
            issuer=base,
            expires_at=epoch + (0 if when == "startup" else 3600000),
            scopes=["read"],
        )
        unrelated = {**entry, "server_name": "other", "access_token": "other-secret"}
        path.write_text(json.dumps({"selected": entry, "other": unrelated}))
        tokens, calls, clients = [], [], []
        model_requests = []
        expired = False

        async def respond(request):
            nonlocal expired
            if request.url.host == "model.fixture.invalid":
                assert request.url.path == "/v1/responses" and request.method == "POST"
                assert request.headers["authorization"] == "Bearer model-secret"
                assert "x-mcp-private" not in request.headers
                assert not any(
                    key.startswith(("x-codex-", "x-openai-"))
                    or key in {"chatgpt-account-id", "openai-beta"}
                    for key in request.headers
                )
                serialized = request.content.decode()
                for secret in (
                    "mcp-access-old",
                    "mcp-access-new",
                    "rotate-once",
                    "other-secret",
                    "mcp-header-secret",
                ):
                    assert secret not in serialized + str(request.headers)
                body = json.loads(serialized)
                assert all(tool["type"] == "function" for tool in body.get("tools", []))
                assert not any(
                    item.get("type") in {"tool_search_output", "compaction", "compaction_trigger"}
                    for item in body["input"]
                )
                model_requests.append(request)
                wire_name = compatible_tool_name(f"mcp__{name}::read")
                tool_names = {tool["name"] for tool in body.get("tools", [])}
                if len(model_requests) == 1 and when == "between_calls":
                    expired = True
                if deferred and len(model_requests) > 1 and not rejected:
                    # Includes the post-call request and successful cold restart:
                    # discovery persists without repeating the external call.
                    assert wire_name in tool_names
                if deferred and len(model_requests) == 1:
                    assert wire_name not in tool_names
                    if not (rejected and when == "startup"):
                        assert "tool_search" in tool_names
                    item = dict(
                        type="function_call",
                        id="search-item",
                        call_id="search-once",
                        name="tool_search",
                        arguments=json.dumps({"query": "read"}),
                    )
                elif len(model_requests) == 1 + int(deferred):
                    if not (rejected and when == "startup"):
                        assert wire_name in tool_names
                    if deferred:
                        assert any(
                            i.get("type") == "function_call_output"
                            and i.get("call_id") == "search-once"
                            for i in body["input"]
                        )
                    item = dict(
                        type="function_call",
                        id="read-item",
                        call_id="read-once",
                        name=wire_name,
                        arguments="{}",
                    )
                else:
                    if len(model_requests) == 2 + int(deferred):
                        outputs = [
                            i
                            for i in body["input"]
                            if i.get("type") == "function_call_output"
                            and i.get("call_id") == "read-once"
                        ]
                        assert len(outputs) == 1
                        assert ("READ_PROOF" in str(outputs)) != rejected
                    item = {"type": "message", "content": [{"type": "output_text", "text": "done"}]}
                packet = {
                    "type": "response.completed",
                    "response": {"id": "fixture", "output": [item]},
                }
                return httpx.Response(200, text=f"data: {json.dumps(packet)}\n\n")
            assert request.url.host == "mcp.fixture.invalid"
            assert "model-secret" not in str(request.headers) + str(request.content)
            if request.url.path == "/token":
                assert request.method == "POST" and "authorization" not in request.headers
                assert "x-mcp-private" not in request.headers
                assert parse_qs(request.content.decode()) == {
                    "grant_type": ["refresh_token"],
                    "client_id": ["client"],
                    "refresh_token": ["rotate-once"],
                    "resource": [base],
                }
                tokens.append(request)
                if slow_refresh:
                    await asyncio.sleep(0.2)
                if rejected:
                    return httpx.Response(
                        400, json={"error": "invalid_grant", "error_description": "rotate-once"}
                    )
                return httpx.Response(
                    200,
                    json=dict(
                        access_token="mcp-access-new",
                        token_type="Bearer",
                        expires_in=3600,
                        refresh_token="next",
                    ),
                )
            if "/.well-known/" in request.url.path:
                assert "authorization" not in request.headers
                return httpx.Response(
                    200,
                    json=(
                        {"resource": base, "authorization_servers": [base]}
                        if "oauth-protected-resource" in request.url.path
                        else {
                            "issuer": base,
                            "authorization_endpoint": base + "/authorize",
                            "token_endpoint": base + "/token",
                        }
                    ),
                )
            if request.method == "GET":
                return httpx.Response(405)
            if request.method == "DELETE":
                return httpx.Response(204)
            packet = json.loads(request.content)
            expected = "mcp-access-new" if tokens else "mcp-access-old"
            assert request.headers["authorization"] == "Bearer " + expected
            assert json.loads(path.read_text())["selected"]["access_token"] == expected
            if "id" not in packet:
                return httpx.Response(202)
            if packet["method"] == "initialize":
                result = {
                    "capabilities": {},
                    "serverInfo": {"name": "fixture", "version": "1"},
                    "protocolVersion": "2025-06-18",
                }
            elif packet["method"] == "tools/list":
                result = {"tools": [{"name": "read", "inputSchema": {"type": "object"}}]}
            else:
                assert packet["method"] == "tools/call"
                calls.append(packet)
                result = {"content": [{"type": "text", "text": "READ_PROOF"}]}
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
            )

        class Carrier(httpx.MockTransport):
            def __init__(self):
                super().__init__(respond)
                self.closed = False
                clients.append(self)

            async def aclose(self):
                self.closed = True

        monkeypatch.setattr(httpx.AsyncClient, "_init_transport", lambda *a, **kw: Carrier())
        monkeypatch.setattr(OwnedHTTPClient, "_init_transport", lambda *a, **kw: Carrier())
        monkeypatch.setenv("NO_PROXY", "*")
        # Expiry progresses deterministically after startup, before tool admission.
        from corki.mcp.oauth_file import FileOAuthToken

        usable = FileOAuthToken.usable
        monkeypatch.setattr(
            FileOAuthToken,
            "usable",
            lambda token: (
                False if expired and token.access_token == "mcp-access-old" else usable(token)
            ),
        )

        class Model:
            count = 0

            async def stream(self, request):
                nonlocal expired
                self.count += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                if self.count == 1 and when == "between_calls":
                    expired = True
                if deferred and self.count == 1:
                    assert not any(tool.name == f"mcp__{name}::read" for tool in request.tools)
                    item = ToolCallItem(
                        ToolCall("search-once", "tool_search", {"query": "read"}), turn, step
                    )
                elif self.count == 1 + int(deferred):
                    item = ToolCallItem(ToolCall("read-once", f"mcp__{name}::read", {}), turn, step)
                else:
                    assert any(
                        isinstance(i, ToolResultItem)
                        and i.call_id == "read-once"
                        and i.is_error == rejected
                        and (("READ_PROOF" in i.content) != rejected)
                        for i in request.items
                    )
                    assert "rotate-once" not in str(request.items)
                    item = AssistantMessageItem("done", turn, step)
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            execution_permissions=None,
            api_key="model-secret",
            api_mode="responses",
            api_base="https://model.fixture.invalid/v1",
            provider_name=name,
            model="gpt-5" if name == "openai" else "fixture-model",
            tool_search_mode="compatible" if deferred else "disabled",
            model_contexts=(
                ModelContextInfo(
                    "gpt-5" if name == "openai" else "fixture-model",
                    supports_search_tool=True,
                ),
            ),
            mcp_oauth_credentials_store="file",
            mcp_servers=(
                MCPServerSettings(
                    name,
                    "http",
                    url=base,
                    http_headers={"x-mcp-private": "mcp-header-secret"},
                    timeout_seconds=0.1 if slow_refresh else 10,
                ),
            ),
        )
        model = Model()

        def create(thread=None):
            return LangGraphRuntime.create(
                settings=settings,
                model=model if model_transport == "scripted" else None,
                registry=ToolRegistry(),
                home_path=tmp_path,
                database_path=tmp_path / "history.db",
                thread_id=thread,
            )

        runtime = create()
        try:
            events = [e async for e in runtime.stream("read")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(tokens) == 1 and len(calls) == (0 if rejected else 1)
            stored = json.loads(path.read_text())
            assert stored["selected"]["refresh_token"] == ("rotate-once" if rejected else "next")
            assert stored["selected"]["scopes"] == ["read"]
            assert stored["other"] == unrelated
            if rejected:
                assert stored["selected"] == entry
            else:
                assert path.stat().st_mode & 0o777 == 0o600
                thread = runtime.thread_id
                await runtime.aclose()
                runtime = create(thread)
                assert isinstance([e async for e in runtime.stream("continue")][-1], TurnCompleted)
                assert len(tokens) == len(calls) == 1
        finally:
            await runtime.aclose()
        assert all(client.closed for client in clients)
        assert len(model_requests) == (
            0 if model_transport == "scripted" else (2 if rejected else 3) + int(deferred)
        )

    asyncio.run(asyncio.wait_for(scenario(), 15))
