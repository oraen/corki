"""Source-mapped redirect behavior through real MCP clients and Runtime admission."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from test_mcp_bound_http import Body, Carrier, runtime_for, server

from corki.mcp.catalog import MCPCatalog, MCPCatalogSource, MCPRegistration
from corki.mcp.client import HttpMCPClient
from corki.mcp.runtime_environment import MCPHTTPEnvironment, MCPRuntimeContext
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ToolResultItem


class RedirectingCarrier(Carrier):
    async def handle_async_request(self, request):
        if request.url.path != "/final":
            self.requests.append(request)
            body = Body(b"redirect body must not be drained")
            self.bodies.append(body)
            return httpx.Response(307, headers={"location": "/final"}, stream=body)
        return await super().handle_async_request(request)


@pytest.mark.parametrize("mode", ["direct", "native", "compatible", "code_mode"])
def test_same_origin_redirect_preserves_runtime_discovery_call_and_cleanup(tmp_path, mode):
    async def scenario():
        carrier = RedirectingCarrier()
        runtime = runtime_for(
            tmp_path, MCPRuntimeContext((MCPHTTPEnvironment("remote", carrier),)), mode=mode
        )
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.refresh_if_dirty()
            assert runtime._mcp_manager.warnings == ()
            events = [event async for event in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted)
            results = [
                item
                for item in runtime._model.requests[-1].items
                if isinstance(item, ToolResultItem)
            ]
            assert "first remote effect" in repr(results)
            assert len(carrier.calls) == 1
            for original in (r for r in carrier.requests if r.url.path != "/final"):
                assert any(
                    r.url.path == "/final"
                    and r.method == original.method
                    and r.content == original.content
                    for r in carrier.requests
                )
        finally:
            await runtime.aclose()
        assert any(r.method == "DELETE" and r.url.path == "/final" for r in carrier.requests)
        assert all(body.closed for body in carrier.bodies)
        assert not carrier.closed
        await carrier.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "agent_plugin,configured", [(False, False), (False, True), (True, False), (True, True)]
)
def test_host_attribution_controls_authenticated_redirects(tmp_path, agent_plugin, configured):
    async def scenario():
        carrier = RedirectingCarrier()
        config = replace(
            server(), http_headers=(("x-api-key", "fixture-secret"),) if configured else ()
        )
        source = MCPCatalogSource("plugin", "fixture", agent_plugin=agent_plugin)
        catalog = MCPCatalog((MCPRegistration(config, source),))
        runtime = runtime_for(
            tmp_path, MCPRuntimeContext((MCPHTTPEnvironment("remote", carrier),)), catalog=catalog
        )
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.refresh_if_dirty()
            stopped = agent_plugin and configured
            assert bool(runtime._mcp_manager.warnings) is stopped
            assert carrier.handshakes == (0 if stopped else 1)
            assert any(r.url.path == "/final" for r in carrier.requests) is not stopped
        finally:
            await runtime.aclose()
            await carrier.aclose()

    asyncio.run(scenario())


def test_attribution_change_reconnects_but_same_attribution_reuses_old_session(tmp_path):
    async def scenario():
        carrier = RedirectingCarrier()
        legacy = MCPCatalogSource("selected_plugin", "fixture")
        catalog = MCPCatalog((MCPRegistration(server(), legacy),))
        runtime = runtime_for(
            tmp_path, MCPRuntimeContext((MCPHTTPEnvironment("remote", carrier),)), catalog=catalog
        )
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.refresh_if_dirty()
            manager = runtime._mcp_manager
            first = manager._clients_by_name["docs"].client
            updated = MCPCatalog((MCPRegistration(server(), replace(legacy, agent_plugin=True)),))
            manager.request_catalog(updated)
            await runtime._refresh_tools()
            second = manager._clients_by_name["docs"].client
            assert first is not second and carrier.handshakes == 2
            assert second._agent_plugin
            manager.request_reconcile((server(),))
            await runtime._refresh_tools()
            assert manager._clients_by_name["docs"].client is second
            assert carrier.handshakes == 2
            assert manager.catalog.servers[0].source.agent_plugin
        finally:
            await runtime.aclose()
            await carrier.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "method,status,next_method",
    [
        ("POST", 301, "GET"),
        ("POST", 302, "GET"),
        ("POST", 303, "GET"),
        ("HEAD", 303, "HEAD"),
        ("POST", 307, "POST"),
        ("POST", 308, "POST"),
        ("GET", 307, "GET"),
        ("DELETE", 307, "DELETE"),
    ],
)
def test_redirect_method_body_headers_and_response_ownership(method, status, next_method):
    async def scenario():
        requests, bodies = [], []

        async def respond(request):
            if requests:
                assert bodies[0].closed
            requests.append(request)
            body = Body(b"" if len(requests) == 1 else b'{"ok":true}')
            bodies.append(body)
            return httpx.Response(
                status if len(requests) == 1 else 200,
                headers={"location": "/final", "content-type": "application/json"},
                stream=body,
            )

        config = replace(server(), environment_id="local", url="http://127.0.0.1/start")
        client = HttpMCPClient(config, transport=httpx.MockTransport(respond))
        headers = httpx.Headers(
            {
                "x-api-key": "secret",
                "proxy-authorization": "proxy",
                "referer": "https://stale.invalid/",
            }
        )
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call"} if method == "POST" else None
        try:
            response = await client._http_request(method, headers, message)
            assert response.status_code == 200
            assert len(requests) == 2
            redirected = requests[1]
            assert redirected.method == next_method
            assert redirected.headers["x-api-key"] == "secret"
            assert "proxy-authorization" not in redirected.headers
            assert redirected.headers["referer"] == "http://127.0.0.1/start"
            if next_method == "POST":
                assert json.loads(redirected.content) == message
            else:
                assert redirected.content == b""
                assert "content-type" not in redirected.headers
            assert all(body.closed for body in bodies)
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "url,location,diagnostic",
    [
        ("https://fixture.invalid/start", "https://other.invalid/private", "different origin"),
        ("https://fixture.invalid/start", "http://fixture.invalid/private", "different origin"),
        ("http://fixture.invalid/start", "/private", "require HTTPS"),
    ],
)
def test_forbidden_redirect_fails_before_destination_request(url, location, diagnostic):
    async def scenario():
        requests, bodies = [], []

        async def respond(request):
            requests.append(request)
            body = Body(b"unread redirect")
            bodies.append(body)
            return httpx.Response(307, headers={"location": location}, stream=body)

        client = HttpMCPClient(
            replace(server(), environment_id="local", url=url),
            transport=httpx.MockTransport(respond),
        )
        try:
            with pytest.raises(Exception, match=diagnostic):
                await client._http_request(
                    "POST",
                    httpx.Headers({"authorization": "Bearer secret"}),
                    {"method": "tools/call"},
                )
            assert len(requests) == 1
            assert bodies[0].closed
        finally:
            await client.aclose()

    asyncio.run(scenario())
