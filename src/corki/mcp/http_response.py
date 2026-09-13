"""HTTP MCP response classification and bounded, owned body collection."""

from collections.abc import Callable, Mapping
from typing import Any

import httpx

from corki.mcp.auth_challenge import insufficient_scope
from corki.mcp.http_stream import owned_response
from corki.mcp.json_rpc import MCPProtocolError, decode_json
from corki.mcp.sse_resume import read_sse_response
from corki.protocol.wire_numbers import dumps_wire

_TRANSIENT_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_LIFECYCLE_METHODS = frozenset(
    {"server/discover", "initialize", "notifications/initialized", "tools/list"}
)


def permits_error_body(response: httpx.Response, *, method: str, has_session: bool) -> bool:
    """Preserve valid RPC errors without hiding transport recovery/auth signals."""
    status = response.status_code
    if status == 404 and has_session:
        return False
    if status == 401 and "www-authenticate" in response.headers:
        return False
    if status == 403 and any(
        insufficient_scope(header) for header in response.headers.get_list("www-authenticate")
    ):
        return False
    if status in _TRANSIENT_STATUSES and method in _LIFECYCLE_METHODS:
        return False
    if not response.headers.get("content-type", "").startswith("application/json"):
        return False
    try:
        value = decode_json(response.content)
    except MCPProtocolError:
        return False
    if not isinstance(value, dict) or value.get("jsonrpc") != "2.0" or "result" in value:
        return False
    error = value.get("error")
    return (
        isinstance(error, dict)
        and type(error.get("code")) is int
        and isinstance(error.get("message"), str)
        and (value.get("id") is None or type(value["id"]) in (int, str))
    )


async def buffered_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: httpx.Headers,
    message: Mapping[str, Any] | None,
    timeout: float | None,
    maximum_bytes: int,
    has_session: bool = False,
    shared_responses: bool = False,
    inbound: Callable[[dict[str, Any]], bool] | None = None,
    executor_timeout_ms: int | None = None,
) -> httpx.Response:
    """Close the carrier on every exit and cap retained decoded response bytes."""
    request = client.build_request(
        method,
        url,
        headers=headers,
        # HTTPX's json encoder would round/reject arbitrary-precision MCP numbers.
        **({"content": dumps_wire(message).encode("utf-8")} if message is not None else {}),
        timeout=timeout,
    )
    request.extensions["corki_mcp_has_session"] = has_session
    if message is not None:
        request.extensions["corki_mcp_method"] = message.get("method")
    if executor_timeout_ms is not None:
        request.extensions["corki_executor_timeout_ms"] = executor_timeout_ms
    response = await client.send(request, stream=True, follow_redirects=False)
    if (
        response.is_success
        and response.status_code not in (202, 204)
        and method == "POST"
        and message is not None
        and (type(message.get("id")) is int or message.get("method") != "notifications/initialized")
        and response.headers.get("content-type", "").lower().startswith("text/event-stream")
    ):
        try:
            value = await read_sse_response(
                client,
                response,
                expected_id=None if shared_responses else message.get("id"),
                maximum_bytes=maximum_bytes,
                timeout=timeout,
                resumable=message.get("method") != "initialize",
                inbound=inbound,
            )
        except httpx.HTTPError:
            # Even a resumed carrier's close failure is after POST acceptance;
            # it must never enter the lifecycle's POST-send retry category.
            raise MCPProtocolError("MCP SSE transport failed after POST acceptance") from None
        response.extensions["corki_mcp_sse_response"] = value
        response._content = b""
        return response
    async with owned_response(response):
        if (
            method == "POST"
            and message is not None
            and message.get("method") in ("initialize", "notifications/initialized")
            and response.is_success
            and response.status_code not in (202, 204)
            and not response.headers.get("content-type", "").lower().startswith("application/json")
        ):
            # Request SSE initialize already took its dedicated path above.
            # Initialized notifications accept JSON or 202/204, never SSE.
            raise MCPProtocolError("Unexpected HTTP content type during MCP initialization")
        body = bytearray()
        headers_only = method == "POST" and (
            response.status_code in (202, 204)
            or response.status_code == 401
            and "www-authenticate" in response.headers
            or response.status_code == 404
            and has_session
        )
        if not headers_only:
            async for chunk in response.aiter_bytes():
                if len(chunk) > maximum_bytes - len(body):
                    raise MCPProtocolError(f"MCP HTTP response exceeds {maximum_bytes} bytes")
                body.extend(chunk)
        # HTTPX aread uses the same cache; its public method is unbounded. Keep
        # decoded bytes on this response, avoiding a second Content-Encoding pass.
        response._content = bytes(body)
        return response
