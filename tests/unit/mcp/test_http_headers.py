import asyncio
import json

import httpx
import pytest

from corki import __version__
from corki.config import MCPServerSettings
from corki.mcp.client import HttpMCPClient, MCPProtocolError
from corki.mcp.http_headers import build_http_headers, request_headers
from corki.mcp.manager import MCPManager
from corki.mcp.reconciliation import connection_identity
from corki.tools import ToolRegistry


def test_http_config_headers_and_env_bearer_reach_post_and_delete_snapshot(monkeypatch):
    async def scenario():
        monkeypatch.setenv("CORKI_MCP_TEST_BEARER", "fixture-token")
        monkeypatch.setenv("CORKI_MCP_TEST_HEADER", "environment")
        seen = []

        def handler(request):
            seen.append(request)
            assert request.headers["authorization"] == "Bearer fixture-token"
            assert request.headers["x-selected"] == "environment"
            assert request.headers["user-agent"] == "fixture-agent/1"
            if request.method == "DELETE":
                assert request.headers["mcp-session-id"] == "fixture-session"
                return httpx.Response(204)
            assert request.headers["accept"] == "text/event-stream, application/json"
            if request.method == "GET":
                assert request.headers["mcp-session-id"] == "fixture-session"
                return httpx.Response(405)
            assert request.headers["content-type"] == "application/json"
            message = json.loads(request.content)
            if "id" not in message:
                return httpx.Response(202)
            return httpx.Response(
                200,
                headers={"mcp-session-id": "fixture-session"},
                json={
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {
                        "serverInfo": {"name": "fixture", "version": "1"},
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                    },
                },
            )

        settings = MCPServerSettings.from_mapping(
            "docs",
            {
                "transport": "http",
                "url": "https://fixture.test",
                "bearer_token_env_var": "CORKI_MCP_TEST_BEARER",
                "http_headers": {
                    "X-Selected": "literal",
                    "Authorization": "literal-auth",
                    "User-Agent": "fixture-agent/1",
                    "Accept": "invalid-choice",
                    "Content-Type": "invalid-choice",
                },
                "env_http_headers": {"x-selected": "CORKI_MCP_TEST_HEADER"},
            },
        )
        client = HttpMCPClient(settings, transport=httpx.MockTransport(handler))
        try:
            await client.start()
            monkeypatch.setenv("CORKI_MCP_TEST_BEARER", "changed-after-initialize")
            monkeypatch.setenv("CORKI_MCP_TEST_HEADER", "changed-after-initialize")
        finally:
            await client.aclose()
        assert len(seen) == 4 and client.is_closed

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [None, "", " \t\u2003", "\udcff", "secret\r\ninvalid", "\x1c"])
def test_invalid_or_absent_env_header_keeps_literal_fallback_without_value_leak(value, caplog):
    settings = MCPServerSettings(
        "docs",
        "http",
        url="https://fixture.test",
        http_headers=(("X-Key", "fallback"),),
        env_http_headers=(("x-key", "KEY"),),
    )
    headers, bearer = build_http_headers(
        settings, inherited={} if value is None else {"KEY": value}
    )
    assert headers["x-key"] == "fallback" and bearer is None
    assert "secret" not in caplog.text
    if value == "\x1c":
        assert "Skipping invalid" in caplog.text  # Not Rust Unicode White_Space.


@pytest.mark.parametrize("name", ["", "bad name", "bad:name", "bad\nname", "é"])
def test_invalid_literal_header_names_skip_without_suppressing_valid_siblings(name, caplog):
    settings = MCPServerSettings(
        "docs", "http", http_headers=((name, "private-value"), ("X-Good", "ok"))
    )
    headers, _ = build_http_headers(settings, inherited={})
    assert dict(headers) == {"user-agent": f"corki-mcp-client/{__version__}", "x-good": "ok"}
    assert "private-value" not in caplog.text


@pytest.mark.parametrize(
    "value,reason",
    [
        (None, "not set"),
        ("", "empty"),
        ("\udcff", "invalid Unicode"),
        ("secret\nvalue", "Invalid bearer"),
    ],
)
def test_bad_explicit_bearer_is_fatal_and_does_not_fall_back_or_leak(value, reason):
    settings = MCPServerSettings(
        "docs",
        "http",
        bearer_token_env_var="TOKEN",
        http_headers=(("Authorization", "literal-auth"),),
    )
    with pytest.raises(ValueError, match=reason) as caught:
        build_http_headers(settings, inherited={} if value is None else {"TOKEN": value})
    assert "secret" not in str(caught.value) and "literal-auth" not in str(caught.value)


def test_utf8_values_exact_whitespace_and_case_insensitive_protocol_overrides():
    settings = MCPServerSettings(
        "docs",
        "http",
        bearer_token_env_var="TOKEN",
        http_headers=(
            ("MCP-SESSION-ID", "spoof"),
            ("mcp-protocol-version", "spoof"),
            ("x-unicode", "原始"),
            ("Accept", "custom"),
            ("Content-Type", "custom"),
        ),
        env_http_headers=(("X-Unicode", "VALUE"),),
    )
    defaults, bearer = build_http_headers(settings, inherited={"TOKEN": "  token  ", "VALUE": "值"})
    post = request_headers(
        defaults, bearer, post=True, protocol_version="2025-06-18", session="real"
    )
    assert dict(post)["authorization"] == "Bearer   token  "
    assert (b"x-unicode", "值".encode()) in [(k.lower(), v) for k, v in post.raw]
    assert post.get_list("mcp-session-id") == ["real"]
    assert post.get_list("mcp-protocol-version") == ["2025-06-18"]
    assert post.get_list("accept") == ["text/event-stream, application/json"]
    delete = request_headers(
        defaults, bearer, post=False, protocol_version="2025-06-18", session="real"
    )
    assert delete["accept"] == "custom" and delete["content-type"] == "custom"
    assert defaults["mcp-session-id"] == "spoof" and defaults["accept"] == "custom"


def test_identity_tracks_named_references_even_if_skipped_or_overridden(monkeypatch):
    settings = MCPServerSettings(
        "docs",
        "http",
        url="https://fixture.test",
        bearer_token_env_var="CORKI_MCP_ID_TOKEN",
        env_http_headers=(("Authorization", "CORKI_MCP_ID_HEADER"),),
    )
    monkeypatch.setenv("CORKI_MCP_ID_TOKEN", "first")
    monkeypatch.setenv("CORKI_MCP_ID_HEADER", "\ninvalid-but-referenced")
    original = connection_identity(settings)
    monkeypatch.setenv("CORKI_MCP_ID_UNRELATED", "changed")
    assert connection_identity(settings) == original
    monkeypatch.setenv("CORKI_MCP_ID_HEADER", "changed")
    assert connection_identity(settings) != original
    second = connection_identity(settings)
    monkeypatch.setenv("CORKI_MCP_ID_TOKEN", "second")
    assert connection_identity(settings) != second


def test_reconciliation_retains_old_authorization_until_in_flight_call_and_delete_finish(
    monkeypatch,
):
    async def scenario():
        clients, calls, deletes = [], [], []
        entered, release = asyncio.Event(), asyncio.Event()
        monkeypatch.setenv("CORKI_HTTP_INFLIGHT", "first")
        config = MCPServerSettings(
            "docs", "http", url="https://fixture.test", bearer_token_env_var="CORKI_HTTP_INFLIGHT"
        )

        def factory(settings):
            session = str(len(clients))
            expected = "first" if not clients else "second"

            async def handler(request):
                assert request.headers["authorization"] == f"Bearer {expected}"
                if request.method == "DELETE":
                    deletes.append(session)
                    return httpx.Response(204)
                message = json.loads(request.content)
                method = message["method"]
                if "id" not in message:
                    return httpx.Response(202)
                if method == "initialize":
                    result = {
                        "serverInfo": {"name": "fixture", "version": "1"},
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {}},
                    }
                elif method == "tools/list":
                    result = {"tools": [{"name": "read", "inputSchema": {"type": "object"}}]}
                else:
                    assert method == "tools/call"
                    calls.append(session)
                    if session == "0":
                        entered.set()
                        await release.wait()
                    result = {"content": [{"type": "text", "text": expected}]}
                return httpx.Response(
                    200,
                    headers={"mcp-session-id": session},
                    json={
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "result": result,
                    },
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(handler))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        manager = MCPManager((config,), ToolRegistry())
        await manager.start()
        old = asyncio.create_task(manager.call_tool("docs", "read", {}))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            monkeypatch.setenv("CORKI_HTTP_INFLIGHT", "second")
            manager.request_reconcile()
            assert (await manager.call_tool("docs", "read", {}))["content"][0]["text"] == "second"
            assert len(clients) == 2 and not clients[0].is_closed and deletes == []
            release.set()
            assert (await old)["content"][0]["text"] == "first"
        finally:
            release.set()
            await asyncio.gather(old, return_exceptions=True)
            await manager.aclose()
        assert calls == ["0", "1"] and deletes == ["0", "1"]
        assert all(c.is_closed for c in clients)

    asyncio.run(scenario())


def test_httpx_network_transport_sends_utf8_header_bytes_and_current_session(monkeypatch):
    async def scenario():
        seen, tasks = [], set()

        async def serve(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            try:
                head = await reader.readuntil(b"\r\n\r\n")
                lines = head.split(b"\r\n")
                headers = {
                    k.lower(): v.strip()
                    for line in lines[1:]
                    if b":" in line
                    for k, v in [line.split(b":", 1)]
                }
                seen.append((lines[0].split()[0], headers))
                body = await reader.readexactly(int(headers.get(b"content-length", b"0")))
                message = json.loads(body) if body else {}
                if message.get("method") == "initialize":
                    result = json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "result": {
                                "serverInfo": {"name": "fixture", "version": "1"},
                                "protocolVersion": "2025-06-18",
                                "capabilities": {},
                            },
                        }
                    ).encode()
                    status = b"200 OK"
                else:
                    result, status = b"", b"204 No Content"
                writer.write(
                    b"HTTP/1.1 " + status + b"\r\nContent-Type: application/json\r\n"
                    b"Mcp-Session-Id: wire\r\nConnection: close\r\nContent-Length: "
                    + str(len(result)).encode()
                    + b"\r\n\r\n"
                    + result
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                tasks.discard(task)

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setenv("CORKI_HTTP_WIRE_TOKEN", "fixture-token")
        client = HttpMCPClient(
            MCPServerSettings(
                "wire",
                "http",
                url=f"http://127.0.0.1:{port}",
                http_headers=(("X-Unicode", "值"),),
                bearer_token_env_var="CORKI_HTTP_WIRE_TOKEN",
            )
        )
        try:
            await client.start()
        finally:
            await client.aclose()
            server.close()
            await server.wait_closed()
            await asyncio.gather(*tasks)
        assert [method for method, _ in seen] == [b"POST", b"POST", b"DELETE"]
        assert all(headers[b"x-unicode"] == "值".encode() for _, headers in seen)
        assert all(headers[b"authorization"] == b"Bearer fixture-token" for _, headers in seen)
        assert all(headers[b"mcp-session-id"] == b"wire" for _, headers in seen[1:])

    asyncio.run(scenario())


def test_local_http_protocol_rejection_cannot_echo_auth_header_in_observation(monkeypatch):
    async def scenario():
        monkeypatch.setenv("CORKI_HTTP_REJECT_TOKEN", "fixture-private-token")
        calls = []

        def handler(request):
            calls.append(request.method)
            raise httpx.LocalProtocolError(
                f"Illegal header value {request.headers['authorization']}"
            )

        client = HttpMCPClient(
            MCPServerSettings(
                "docs",
                "http",
                url="https://fixture.test",
                bearer_token_env_var="CORKI_HTTP_REJECT_TOKEN",
            ),
            transport=httpx.MockTransport(handler),
        )
        try:
            with pytest.raises(MCPProtocolError) as caught:
                await client.start()
            assert "fixture-private-token" not in str(caught.value)
            assert "request framing" in str(caught.value)
            assert calls == ["POST"]
        finally:
            await client.aclose()

    asyncio.run(scenario())
