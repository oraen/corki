"""Request-local SSE continuation by GET; never replay an accepted request POST."""

import logging
from asyncio import sleep
from collections.abc import Callable
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any

import httpx

from corki.mcp.http_stream import owned_response
from corki.mcp.initialization import initialization_event
from corki.mcp.json_rpc import MCPProtocolError
from corki.mcp.sse import SseEventTooLarge, SseParser, message_from_event, response_from_event

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _ResumeState:
    last_event_id: str | None = None
    server_retry: float | None = None


async def read_sse_response(
    client: httpx.AsyncClient,
    initial: httpx.Response,
    *,
    expected_id: int | None,
    maximum_bytes: int,
    timeout: float | None,
    resumable: bool,
    receive: Callable[[dict[str, Any]], None] | None = None,
    inbound: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """Own all carriers under the caller's deadline and frozen request identity."""
    state = _ResumeState()
    headers = httpx.Headers(initial.request.headers, encoding="utf-8")
    for name in ("content-length", "content-type", "transfer-encoding"):
        headers.pop(name, None)
    response = initial
    while True:
        interrupted = False
        async with owned_response(response):
            parser = SseParser(maximum_bytes)
            try:
                async with aclosing(response.aiter_bytes()) as chunks:
                    async for chunk in chunks:
                        for event in parser.feed(chunk):
                            if event.id is not None:
                                state.last_event_id = event.id
                            if event.retry is not None:
                                state.server_retry = event.retry / 1000
                            if resumable and inbound is not None:
                                message = message_from_event(event)
                                try:
                                    if message is not None and inbound(message):
                                        continue
                                except MCPProtocolError:
                                    # Ordinary SSE skips malformed typed messages, as rmcp does.
                                    continue
                            value = (
                                response_from_event(event, expected_id)
                                if resumable
                                else initialization_event(event)
                            )
                            if value is not None:
                                if receive is None:
                                    return value
                                receive(value)
            except SseEventTooLarge:
                raise
            except (httpx.TransportError, MCPProtocolError) as error:
                if not resumable or state.last_event_id is None and receive is None:
                    if isinstance(error, MCPProtocolError):
                        raise
                    raise MCPProtocolError(
                        "MCP SSE stream failed before its final response"
                    ) from None
                interrupted = True
                logger.debug("Resuming interrupted MCP SSE: %s", type(error).__name__)
            if not resumable or state.last_event_id is None and receive is None:
                raise MCPProtocolError("MCP event stream closed before its final response")
        # The previous carrier is closed before waiting or acquiring another one.
        if not interrupted:
            delay = state.server_retry if state.server_retry is not None else 1.0
            state.server_retry = None
            await sleep(delay)
        response = await _reconnect(client, initial.request.url, headers, state, timeout)


async def _reconnect(
    client: httpx.AsyncClient,
    url: httpx.URL,
    headers: httpx.Headers,
    state: _ResumeState,
    timeout: float | None,
) -> httpx.Response:
    values = httpx.Headers(headers, encoding="utf-8")
    if state.last_event_id is not None:
        values["last-event-id"] = state.last_event_id
    retries = 0
    while True:
        try:
            request = client.build_request("GET", url, headers=values, timeout=timeout)
            response = await client.send(request, stream=True, follow_redirects=False)
        except (httpx.HTTPError, MCPProtocolError, ValueError) as error:
            # Redirect/helper rejection is an adapter error for this GET, not
            # permission to abandon SSE recovery or replay the accepted POST.
            # Match the native reconnect handler's error/backoff path; the outer
            # operation budget still bounds retries and cancellation propagates.
            logger.debug("MCP SSE reconnect send failed: %s", type(error).__name__)
        else:
            content_type = response.headers.get("content-type", "").lower()
            if response.is_success and content_type.startswith(
                ("text/event-stream", "application/json")
            ):
                return response
            # Reconnect errors, including 404, cannot escape into POST recovery.
            async with owned_response(response):
                logger.debug("MCP SSE reconnect rejected: HTTP %s", response.status_code)
        retries += 1
        # A request deadline cancels this sleep. Avoid integer/float overflow on
        # unreachable huge retry counts without imposing a smaller retry limit.
        delay = float(2 ** min(retries, 63))
        await sleep(max(delay, state.server_retry or 0.0))
