"""Real Runtime/HTTP startup: OAuth must reach host authorization, not silent failure.

Provider HTTP uses MockTransport; accepted login uses a real loopback callback.
No real browser, real token, external registration or OS keyring is used.
All three store policies run the actual installation, login and cold client paths.
"""

import asyncio
import base64
import hashlib
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.http_client import OwnedHTTPClient
from corki.mcp.catalog import MCPCatalog, MCPRegistration
from corki.mcp.tool_catalog_cache import MCPToolCatalogCache
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import ContextItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    "mode", ["oauth", "bearer", "public", "public_oauth", "legacy_oauth", "accept", "revoked"]
)
@pytest.mark.parametrize("cold_store", ["file", "auto", "keyring"])
def test_http_dependency_reaches_authorization_or_ready_tools(
    tmp_path, monkeypatch, mode, cold_store
):
    async def scenario():
        base = "https://oauth-fixture.invalid"
        url = base + "/mcp"
        requests, prompts, model_requests, transports = [], [], [], []
        keyring_values, reads = {}, []

        def keyring_read(service, key):
            assert service == "Corki MCP Credentials"
            reads.append((service, key))
            return keyring_values.get(key)

        def keyring_write(service, key, value):
            assert service == "Corki MCP Credentials"
            keyring_values[key] = value

        backend = SimpleNamespace(priority=1, get_password=keyring_read, set_password=keyring_write)
        monkeypatch.setattr("keyring.get_keyring", lambda: backend)
        skill = tmp_path / ".corki/skills/oauth-guide/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: oauth-guide\ndescription: fixture\n---\nOAUTH_BODY")
        metadata = skill.parent / "agents/openai.yaml"
        metadata.parent.mkdir()
        metadata.write_text(
            "dependencies:\n  tools:\n    - type: mcp\n      value: oauth_fixture\n"
            "      transport: streamable_http\n      url: " + url + "\n"
        )

        def handle(request):
            assert request.url.host == "oauth-fixture.invalid", "unexpected external HTTP"
            requests.append(request)
            path = request.url.path
            if "/.well-known/" in path:
                if mode == "public":
                    return httpx.Response(404)
                if "oauth-protected-resource" in path:
                    return httpx.Response(
                        200, json={"resource": url, "authorization_servers": [base]}
                    )
                return httpx.Response(
                    200,
                    json={
                        **({"issuer": base} if mode != "legacy_oauth" else {}),
                        "authorization_endpoint": base + "/authorize",
                        "token_endpoint": base + "/token",
                        "registration_endpoint": base + "/register",
                        "response_types_supported": ["code"],
                        "code_challenge_methods_supported": ["S256"],
                        "token_endpoint_auth_methods_supported": ["none"],
                        "authorization_response_iss_parameter_supported": mode != "legacy_oauth",
                    },
                )
            if path == "/register":
                return httpx.Response(
                    201,
                    json={
                        "client_id": "fixture-client",
                        "token_endpoint_auth_method": "none",
                        "redirect_uris": json.loads(request.content).get("redirect_uris", []),
                    },
                )
            if path == "/token":
                assert mode == "accept", "declined authorization must not exchange tokens"
                fields = parse_qs(request.content.decode())
                authorization = parse_qs(urlsplit(prompts[0].params["url"]).query)
                challenge = (
                    base64.urlsafe_b64encode(
                        hashlib.sha256(fields["code_verifier"][0].encode()).digest()
                    )
                    .rstrip(b"=")
                    .decode()
                )
                assert authorization["code_challenge"] == [challenge]
                assert fields["code"] == ["fixture-code"]
                assert fields["resource"] == [url]
                assert "authorization" not in request.headers
                return httpx.Response(
                    200,
                    json={
                        "access_token": "fixture-token",
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    },
                )
            assert path == "/mcp"
            authorized = (
                mode in {"public", "public_oauth"}
                or request.headers.get("authorization") == "Bearer fixture-token"
            )
            if not authorized:
                metadata_url = base + "/.well-known/oauth-protected-resource/mcp"
                return httpx.Response(
                    401,
                    headers={
                        "WWW-Authenticate": f'Bearer resource_metadata="{metadata_url}"',
                    },
                )
            if request.method == "DELETE":
                return httpx.Response(204)
            if request.method == "GET":
                return httpx.Response(405)
            message = json.loads(request.content)
            if "id" not in message:
                return httpx.Response(202)
            method = message["method"]
            if method == "initialize":
                result = {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fixture", "version": "1"},
                }
            elif method == "tools/list":
                result = {"tools": [{"name": "lookup", "inputSchema": {"type": "object"}}]}
            elif method == "tools/call":
                assert mode == "accept"
                assert message["params"]["name"] == "lookup"
                result = {"content": [{"type": "text", "text": "OAUTH_LOOKUP_RESULT"}]}
            else:
                raise AssertionError(method)
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": message["id"], "result": result}
            )

        class Transport(httpx.MockTransport):
            def __init__(self):
                super().__init__(handle)
                self.closed = False
                transports.append(self)

            async def aclose(self):
                self.closed = True
                await super().aclose()

        monkeypatch.setenv("NO_PROXY", "*")
        monkeypatch.setenv("CORKI_TEST_OAUTH_BEARER", "fixture-token")
        monkeypatch.setattr(
            httpx.AsyncClient, "_init_transport", lambda *args, **kwargs: Transport()
        )
        # A real OwnedHTTPClient wraps a real HTTPX pool. The fixture supplies
        # an explicit in-memory carrier instead, so do not inspect it as a pool.
        monkeypatch.setattr(OwnedHTTPClient, "_init_transport", lambda *args, **kwargs: Transport())

        def no_browser(*args, **kwargs):
            raise AssertionError("host-declined login must not launch a browser")

        monkeypatch.setattr("webbrowser.open", no_browser)

        class Model:
            async def stream(self, request):
                model_requests.append(request)
                if mode == "accept" and len(model_requests) in {2, 4}:
                    assert any(t.name == "mcp__oauth_fixture::lookup" for t in request.tools)
                    turn = next(item.turn_id for item in reversed(request.items) if item.turn_id)
                    call = ToolCall(new_tool_call_id(), "mcp__oauth_fixture::lookup", {})
                    yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))
                    return
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                tmp_path,
                execution_permissions=None,
                tool_search_mode="disabled",
                mcp_oauth_credentials_store=cold_store,
                mcp_servers=(
                    MCPServerSettings(
                        "oauth_fixture",
                        "http",
                        url=url,
                        bearer_token_env_var="CORKI_TEST_OAUTH_BEARER",
                    ),
                )
                if mode == "bearer"
                else (),
            ),
            model=Model(),
            registry=ToolRegistry(),
            # Each mode models a different provider state at the same fixture
            # URL. Do not reuse another case's process-wide definition snapshot.
            mcp_tool_catalog_cache=MCPToolCatalogCache(),
            home_path=tmp_path / "home",
            database_path=tmp_path / "session.db",
        )

        async def decline(request):
            prompts.append(request)
            if mode in {"accept", "revoked"}:
                fields = parse_qs(urlsplit(request.params["url"]).query)
                redirect = urlsplit(fields["redirect_uri"][0])
                reader, writer = await asyncio.open_connection(redirect.hostname, redirect.port)
                try:
                    target = (
                        redirect.path
                        + "?"
                        + urlencode(
                            {"state": fields["state"][0], "iss": base, "code": "fixture-code"}
                        )
                    )
                    writer.write(f"GET {target} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode())
                    await writer.drain()
                    assert (await reader.read()).startswith(b"HTTP/1.1 200")
                finally:
                    writer.close()
                    await writer.wait_closed()
            if mode == "revoked":
                runtime._mcp_manager.request_catalog(
                    MCPCatalog(
                        (
                            MCPRegistration(
                                MCPServerSettings("oauth_fixture", "http", url=url, enabled=False)
                            ),
                        )
                    )
                )
            runtime.respond_mcp_elicitation(
                request.server_name,
                request.request_id,
                "accept" if mode in {"accept", "revoked"} else "decline",
            )

        runtime.set_mcp_elicitation_handler(decline)
        try:
            first = [event async for event in runtime.stream("$oauth-guide")]
            assert isinstance(first[-1], TurnCompleted)
            second = [event async for event in runtime.stream("continue")]
            assert isinstance(second[-1], TurnCompleted)
            assert any(
                isinstance(item, ContextItem) and "OAUTH_BODY" in item.content
                for item in model_requests[0].items
            )
            exposed = any(
                tool.name == "mcp__oauth_fixture::lookup" for tool in model_requests[-1].tools
            )
            assert exposed == (mode not in {"oauth", "legacy_oauth", "revoked"}), (
                runtime._mcp_manager._preparation.server_warnings
            )
            needs_login = mode in {"oauth", "public_oauth", "legacy_oauth", "accept", "revoked"}
            if needs_login and not prompts:
                assert any(r.url.path == "/mcp" for r in requests), "fixture never reached MCP HTTP"
            assert len(prompts) == (1 if needs_login else 0), (
                "OAuth never reached host authorization"
            )
            if needs_login:
                assert prompts[0].params.get("mode") == "url"
                assert any(r.url.path.startswith("/.well-known/") for r in requests)
            assert sum(r.url.path == "/token" for r in requests) == (1 if mode == "accept" else 0)
            if mode == "revoked":
                assert not (tmp_path / "home/.credentials.json").exists()
            if mode == "accept":
                assert any(
                    isinstance(item, ToolResultItem) and "OAUTH_LOOKUP_RESULT" in item.content
                    for item in model_requests[2].items
                ), [
                    item.content
                    for item in model_requests[2].items
                    if isinstance(item, ToolResultItem)
                ]
                saved = (
                    json.loads((tmp_path / "home/.credentials.json").read_text())
                    if cold_store == "file"
                    else {key: json.loads(value) for key, value in keyring_values.items()}
                )
                assert len(saved) == 1
                token = next(iter(saved.values()))
                assert token["server_url"] == url
                assert token["access_token"] == "fixture-token"
                assert "fixture-token" not in repr(model_requests)
        finally:
            await runtime.aclose()
            assert transports and all(t.closed for t in transports)

        if mode == "accept":
            file = tmp_path / "home/.credentials.json"
            before = file.read_bytes() if file.exists() else None
            keyring_before = dict(keyring_values)
            reads.clear()
            cold = LangGraphRuntime.create(
                settings=CorkiSettings(
                    tmp_path,
                    execution_permissions=None,
                    skills_enabled=False,
                    tool_search_mode="disabled",
                    mcp_oauth_credentials_store=cold_store,
                    mcp_servers=(MCPServerSettings("oauth_fixture", "http", url=url),),
                ),
                model=Model(),
                registry=ToolRegistry(),
                mcp_tool_catalog_cache=MCPToolCatalogCache(),
                home_path=tmp_path / "home",
                database_path=tmp_path / "cold.db",
            )
            try:
                events = [event async for event in cold.stream("lookup after restart")]
                assert isinstance(events[-1], TurnCompleted)
                assert any(
                    isinstance(item, ToolResultItem) and "OAUTH_LOOKUP_RESULT" in item.content
                    for item in model_requests[-1].items
                )
                assert sum(r.url.path == "/token" for r in requests) == 1
                assert sum(r.url.path == "/register" for r in requests) == 1
                assert (
                    sum(
                        r.url.path == "/mcp"
                        and r.method == "POST"
                        and json.loads(r.content).get("method") == "tools/call"
                        for r in requests
                    )
                    == 2
                )
                assert (file.read_bytes() if file.exists() else None) == before
                assert keyring_values == keyring_before
                assert bool(reads) == (cold_store != "file")
                assert "fixture-token" not in repr(model_requests)
            finally:
                await cold.aclose()
                assert all(t.closed for t in transports)

    asyncio.run(asyncio.wait_for(scenario(), 10))
