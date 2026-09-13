"""Inspect every MCP redirect before forwarding credentials or tool-call bodies."""

import asyncio
import ipaddress
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from corki.config.mcp_url import join_http_url
from corki.http_client import OwnedHTTPClient
from corki.mcp.http_stream import owned_response
from corki.mcp.json_rpc import MCPProtocolError

SendRequest = Callable[[httpx.Request], Awaitable[httpx.Response]]
SendHop = Callable[[httpx.Request, SendRequest], Awaitable[httpx.Response]]
_REDIRECTS = {301, 302, 303, 307, 308}
_BODY_HEADERS = {"content-type", "content-length", "content-encoding", "transfer-encoding"}


@dataclass(frozen=True, slots=True)
class MCPRedirectPolicy:
    """Host attribution and configured headers, never server-provided authority."""

    agent_plugin: bool = False
    has_configured_headers: bool = False

    def follows(self, request: httpx.Request) -> bool:
        """Discovery and modern protocol stop; legacy authenticated requests may follow."""
        return not (
            request.extensions.get("corki_mcp_method") == "server/discover"
            or request.headers.get("mcp-protocol-version") == "2026-07-28"
            or self.agent_plugin
            and (self.has_configured_headers or "authorization" in request.headers)
        )


def _origin(url: httpx.URL) -> tuple[str, bytes, int]:
    port = url.port if url.port is not None else 443 if url.scheme == "https" else 80
    return url.scheme, url.raw_host, port


def _next_request(
    request: httpx.Request, response: httpx.Response, origin: tuple, *, agent_plugin: bool = False
) -> httpx.Request:
    # HTTPX Headers.get joins duplicates; native uses the first Location header.
    location = response.headers.get_list("location")[0]
    try:
        target = (
            httpx.URL(join_http_url(str(request.url), location))
            if agent_plugin
            else request.url.join(location)
        )
    except (httpx.InvalidURL, ValueError) as exc:
        raise MCPProtocolError("invalid MCP HTTP redirect URL") from exc
    if _origin(target) != origin:
        raise MCPProtocolError("MCP HTTP redirect to a different origin is not allowed")
    if target.scheme == "http" and target.host != "localhost":
        try:
            ipaddress.ip_address(target.host)
        except ValueError:
            raise MCPProtocolError(
                "MCP HTTP redirects for non-loopback hostnames require HTTPS"
            ) from None
    method, body = request.method, request.content
    drop_body = (
        response.status_code == 303 or response.status_code in (301, 302) and method == "POST"
    )
    headers = httpx.Headers(request.headers, encoding="utf-8")
    if drop_body:
        if method != "HEAD":
            method = "GET"
        body = b""
        for name in _BODY_HEADERS:
            headers.pop(name, None)
    if request.url.scheme != "https":
        headers.pop("proxy-authorization", None)
    headers.pop("referer", None)
    referer = str(request.url.copy_with(username="", password="", fragment=None))
    if all(byte == 9 or byte >= 32 and byte != 127 for byte in referer.encode("utf-8")):
        headers["referer"] = referer
    return httpx.Request(
        method, target, headers=headers, content=body, extensions=dict(request.extensions)
    )


async def follow_mcp_redirects(
    request: httpx.Request, send: SendRequest, policy: MCPRedirectPolicy
) -> httpx.Response:
    """Release each abandoned stream, sharing the original deadline across all hops."""
    if not policy.follows(request):
        return await send(request)
    origin = _origin(request.url)
    budget = request.extensions.get("corki_executor_timeout_ms")
    deadline = asyncio.get_running_loop().time() + budget / 1000 if budget is not None else None
    redirects = 0
    await request.aread()
    while True:
        if deadline is not None:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise MCPProtocolError("MCP HTTP request timed out")
            request.extensions["corki_executor_timeout_ms"] = max(1, int(remaining * 1000))
        response = await send(request)
        if response.status_code not in _REDIRECTS or "location" not in response.headers:
            return response
        async with owned_response(response):
            next_request = _next_request(
                request, response, origin, agent_plugin=policy.agent_plugin
            )
            if redirects >= 10:
                raise MCPProtocolError("MCP HTTP request exceeded the redirect limit")
        request = next_request
        redirects += 1


class MCPHttpSession(OwnedHTTPClient):
    """Apply the same redirect/inner-helper contract to POST, GET, DELETE and SSE resume."""

    def __init__(
        self, *, redirect_policy: Callable[[], MCPRedirectPolicy], send_hop: SendHop, **kwargs
    ):
        super().__init__(**kwargs)
        self._redirect_policy = redirect_policy
        self._send_hop = send_hop

    async def send(
        self, request: httpx.Request, *, stream: bool = False, **kwargs
    ) -> httpx.Response:
        # Never delegate automatic redirect following to HTTPX: the next origin
        # must be approved before any transport can transmit sensitive payloads.
        kwargs["follow_redirects"] = False

        async def raw_send(candidate: httpx.Request) -> httpx.Response:
            return await super(MCPHttpSession, self).send(candidate, stream=True, **kwargs)

        async def hop(candidate: httpx.Request) -> httpx.Response:
            return await self._send_hop(candidate, raw_send)

        response = await follow_mcp_redirects(request, hop, self._redirect_policy())
        if not stream:
            async with owned_response(response):
                await response.aread()
        return response
