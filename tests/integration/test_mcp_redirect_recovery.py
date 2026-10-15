"""Redirect hops retain helper refresh and SSE continuation ownership."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from test_mcp_bound_http import Body, server

from corki.mcp.client import HttpMCPClient
from corki.mcp.header_helper import HeaderHelper


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("agent_plugin", [False, True])
def test_helper_auth_refresh_runs_inside_each_validated_hop(
    tmp_path, monkeypatch, scheme, agent_plugin
):
    async def scenario():
        runs, requests, bodies = [], [], []

        async def helper(*args):
            runs.append(1)
            return {
                "authorization": "Bearer old" if len(runs) == 1 else "Bearer new",
                "proxy-authorization": "proxy",
            }

        monkeypatch.setattr("corki.mcp.header_helper.run_helper", helper)

        async def respond(request):
            assert all(body.closed for body in bodies)
            requests.append(request)
            body = Body(b'{"ok":true}')
            bodies.append(body)
            status = (
                307
                if request.url.path == "/start"
                else 401
                if request.headers["authorization"] == "Bearer old"
                else 200
            )
            return httpx.Response(
                status,
                headers={"location": "/final", "content-type": "application/json"},
                stream=body,
            )

        config = replace(
            server(),
            environment_id="local",
            url=f"{scheme}://127.0.0.1/start",
            http_headers_helper="fixture",
            cwd=tmp_path,
        )
        client = HttpMCPClient(
            config, transport=httpx.MockTransport(respond), agent_plugin=agent_plugin
        )
        try:
            call = client._http_request(
                "POST", client._request_headers(post=True), {"method": "tools/call"}
            )
            if scheme == "http":
                with pytest.raises(ValueError, match="Proxy-Authorization"):
                    await call
                assert len(requests) == len(runs) == 1
            else:
                assert (await call).status_code == 200
                assert len(requests) == 3 and len(runs) == 2
                assert [r.headers["authorization"] for r in requests] == [
                    "Bearer old",
                    "Bearer old",
                    "Bearer new",
                ]
                assert all(r.headers["proxy-authorization"] == "proxy" for r in requests)
            assert all(body.closed for body in bodies)
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_redirected_sse_get_resumes_without_replaying_tool_post():
    async def scenario():
        requests, bodies = [], []
        identity = None

        async def respond(request):
            nonlocal identity
            assert all(body.closed for body in bodies)
            requests.append(request)
            if request.method == "DELETE":
                return httpx.Response(204)
            if request.method == "POST":
                identity = json.loads(request.content)["id"]
                status, headers, data = (
                    200,
                    {"content-type": "text/event-stream"},
                    b"id: cursor\nretry: 0\nevent: ping\n\n",
                )
            elif request.url.path == "/start":
                assert request.headers["last-event-id"] == "cursor"
                status, headers, data = 307, {"location": "/events"}, b"unread"
            else:
                assert (
                    request.url.path == "/events" and request.headers["last-event-id"] == "cursor"
                )
                assert request.headers["authorization"] == "Bearer fixture"
                status, headers = 200, {"content-type": "text/event-stream"}
                data = (
                    b"data: "
                    + json.dumps(
                        {"jsonrpc": "2.0", "id": identity, "result": {"content": []}}
                    ).encode()
                    + b"\n\n"
                )
            body = Body(data)
            bodies.append(body)
            return httpx.Response(status, headers=headers, stream=body)

        config = replace(
            server(),
            environment_id="local",
            url="https://fixture.invalid/start",
            headers=(("authorization", "Bearer fixture"),),
        )
        client = HttpMCPClient(config, transport=httpx.MockTransport(respond))
        client._initialized = True
        client._protocol_version = "2025-06-18"
        try:
            assert await client.call_tool("read", {}) == {"content": []}
            assert [(r.method, r.url.path) for r in requests] == [
                ("POST", "/start"),
                ("GET", "/start"),
                ("GET", "/events"),
            ]
            assert all(body.closed for body in bodies)
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_helper_explicit_zero_port_does_not_authorize_default_https_port(tmp_path, monkeypatch):
    async def scenario():
        async def forbidden(*args):
            raise AssertionError("helper credentials must not be read for a different origin")

        monkeypatch.setattr("corki.mcp.header_helper.run_helper", forbidden)
        helper = HeaderHelper("https://fixture.invalid:0/start", "fixture", tmp_path)

        async def send(headers, timeout):
            assert "authorization" not in headers
            return httpx.Response(200)

        try:
            result = await helper.request(
                "GET", "https://fixture.invalid/final", httpx.Headers(), 1, send
            )
            assert result.status_code == 200
        finally:
            await helper.aclose()

    asyncio.run(scenario())
