"""Native redirect policy, bounded chains and cancelled stream cleanup."""

import asyncio

import httpx
import pytest

from corki.mcp.http_redirect import MCPRedirectPolicy, follow_mcp_redirects
from corki.mcp.json_rpc import MCPProtocolError


class Stream(httpx.AsyncByteStream):
    def __init__(self, closing=None, release=None):
        self.closed = False
        self.closing, self.release = closing, release

    async def __aiter__(self):
        raise AssertionError("redirect bodies must not be read")
        yield b""

    async def aclose(self):
        if self.closing is not None:
            self.closing.set()
            await self.release.wait()
        self.closed = True


@pytest.mark.parametrize(
    "protocol,method", [("2026-07-28", "tools/call"), (None, "server/discover")]
)
def test_modern_protocol_and_discovery_stop_without_validating_location(protocol, method):
    async def scenario():
        body = Stream()
        response = httpx.Response(307, headers={"location": "https://other.invalid"}, stream=body)
        calls = []

        async def send(request):
            calls.append(request)
            return response

        request = httpx.Request(
            "POST", "https://fixture.invalid", extensions={"corki_mcp_method": method}
        )
        if protocol is not None:
            request.headers["mcp-protocol-version"] = protocol
        assert await follow_mcp_redirects(request, send, MCPRedirectPolicy()) is response
        assert len(calls) == 1 and not body.closed
        await response.aclose()

    asyncio.run(scenario())


def test_shared_deadline_and_ten_hop_limit_release_every_stream():
    async def scenario(timed):
        requests, bodies = [], []

        async def send(request):
            requests.append(request)
            if timed:
                await asyncio.sleep(0.02)
            body = Stream()
            bodies.append(body)
            return httpx.Response(307, headers={"location": "/loop"}, stream=body)

        request = httpx.Request("POST", "https://fixture.invalid/start", content=b"effect")
        if timed:
            request.extensions["corki_executor_timeout_ms"] = 30
        with pytest.raises(MCPProtocolError, match="timed out" if timed else "redirect limit"):
            await follow_mcp_redirects(request, send, MCPRedirectPolicy())
        assert all(body.closed for body in bodies)
        if timed:
            assert 1 <= len(requests) <= 2
            if len(requests) == 2:
                assert (
                    requests[1].extensions["corki_executor_timeout_ms"]
                    < requests[0].extensions["corki_executor_timeout_ms"]
                )
        else:
            assert len(requests) == 11

    asyncio.run(scenario(False))
    asyncio.run(scenario(True))


def test_cancelled_redirect_close_joins_owned_stream_before_propagating():
    async def scenario():
        closing, release = asyncio.Event(), asyncio.Event()
        body = Stream(closing, release)
        calls = []

        async def send(request):
            calls.append(request)
            return httpx.Response(307, headers={"location": "/final"}, stream=body)

        task = asyncio.create_task(
            follow_mcp_redirects(
                httpx.Request("GET", "https://fixture.invalid"), send, MCPRedirectPolicy()
            )
        )
        await asyncio.wait_for(closing.wait(), 1)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert body.closed and len(calls) == 1

    asyncio.run(scenario())


def test_first_location_and_sanitized_referer_are_preserved():
    async def scenario():
        requests = []

        async def send(request):
            requests.append(request)
            return httpx.Response(
                307 if len(requests) == 1 else 200,
                headers=[("location", "/final"), ("location", "https://other.invalid")],
                stream=Stream(),
            )

        request = httpx.Request(
            "POST", "https://user:secret@fixture.invalid/start#fragment", content=b"effect"
        )
        response = await follow_mcp_redirects(request, send, MCPRedirectPolicy())
        assert requests[1].url.path == "/final"
        assert requests[1].headers["referer"] == "https://fixture.invalid/start"
        assert requests[1].content == b"effect"
        await response.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("agent_plugin", [False, True])
@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("authorization", [False, True])
def test_legacy_and_agent_attribution_have_distinct_header_rules(
    agent_plugin, configured, authorization
):
    async def scenario():
        calls = []

        async def send(request):
            calls.append(request)
            return httpx.Response(
                307 if len(calls) == 1 else 200, headers={"location": "/final"}, stream=Stream()
            )

        request = httpx.Request("POST", "https://fixture.invalid/start", content=b"effect")
        if authorization:
            request.headers["authorization"] = "Bearer fixture"
        result = await follow_mcp_redirects(
            request, send, MCPRedirectPolicy(agent_plugin, configured)
        )
        stopped = agent_plugin and (configured or authorization)
        assert result.status_code == (307 if stopped else 200)
        assert len(calls) == (1 if stopped else 2)
        await result.aclose()

    asyncio.run(scenario())


def test_explicit_zero_port_is_not_the_https_default_origin():
    async def scenario():
        calls = []

        async def send(request):
            calls.append(request)
            return httpx.Response(
                307, headers={"location": "https://fixture.invalid:0/private"}, stream=Stream()
            )

        with pytest.raises(MCPProtocolError, match="different origin"):
            await follow_mcp_redirects(
                httpx.Request("POST", "https://fixture.invalid/start"), send, MCPRedirectPolicy()
            )
        assert len(calls) == 1

    asyncio.run(scenario())
