import asyncio
import gzip
import json

import httpx
import pytest

from corki.config import MCPServerSettings
from corki.mcp.client import HttpMCPClient, MCPProtocolError


def error_packet(**changes):
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32602, "message": "repair argument"},
        **changes,
    }


@pytest.mark.parametrize("status", [400, 401, 403, 404, 408, 429, 500, 502, 503, 504])
def test_tool_http_status_preserves_json_rpc_error_without_retry(status):
    async def scenario():
        seen = []

        def handle(request):
            seen.append(request)
            return httpx.Response(status, json=error_packet())

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(handle),
        )
        try:
            with pytest.raises(MCPProtocolError, match="MCP error -32602: repair argument"):
                await client.request("tools/call", {})
            assert len(seen) == 1
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["initialize", "tools/list", "notifications/initialized"])
@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_lifecycle_transient_status_is_not_downgraded_to_rpc_error(method, status):
    async def scenario():
        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(lambda _: httpx.Response(status, json=error_packet())),
        )
        try:
            with pytest.raises(httpx.HTTPStatusError) as caught:
                await client.request(method, {})
            assert caught.value.response.status_code == status
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status,headers,session,packet",
    [
        (401, {"www-authenticate": ""}, None, error_packet()),
        (403, {"www-authenticate": 'Bearer error="insufficient_scope"'}, None, error_packet()),
        (404, {}, "existing", error_packet()),
        (400, {"content-type": "text/plain"}, None, error_packet()),
        (400, {}, None, error_packet(jsonrpc="1.0")),
        (400, {}, None, error_packet(error={"code": True, "message": "bad"})),
        (400, {}, None, error_packet(error={"code": -1, "message": 1})),
        (400, {}, None, {"jsonrpc": "2.0", "id": 1, "result": {}}),
    ],
)
def test_status_error_precedence_and_invalid_error_envelopes(status, headers, session, packet):
    async def scenario():
        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(
                lambda _: httpx.Response(status, headers=headers, json=packet)
            ),
        )
        client._session_id = session
        try:
            with pytest.raises(httpx.HTTPStatusError):
                await client.request("tools/call", {})
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("response_id", [None, "wrong", 2])
def test_error_status_does_not_bypass_request_identity(response_id):
    async def scenario():
        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid", timeout_seconds=0.03),
            transport=httpx.MockTransport(
                lambda _: httpx.Response(400, json=error_packet(id=response_id))
            ),
        )
        try:
            with pytest.raises(TimeoutError):
                await client.request("tools/call", {})
        finally:
            await client.aclose()

    asyncio.run(scenario())


class ProbeStream(httpx.AsyncByteStream):
    def __init__(self, chunks=(), failure=None):
        self.chunks, self.failure = chunks, failure
        self.read = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.read += 1
            yield chunk
        if self.failure is not None:
            raise self.failure

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("status", [200, 400, 503])
def test_collection_cap_stops_reading_before_eof_and_closes(monkeypatch, status):
    async def scenario():
        monkeypatch.setattr("corki.mcp.client.MAX_HTTP_RESPONSE_BYTES", 64)
        stream = ProbeStream([b"x" * 32] * 3, AssertionError("must not read past cap"))
        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(lambda _: httpx.Response(status, stream=stream)),
        )
        try:
            with pytest.raises(MCPProtocolError, match="exceeds"):
                await client.request("tools/call", {})
            assert stream.read == 3 and stream.closed
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [202, 204])
def test_accepted_notification_does_not_read_body_or_install_session(status):
    async def scenario():
        stream = ProbeStream(failure=AssertionError("accepted body must not be read"))
        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    status, headers={"mcp-session-id": "ignored"}, stream=stream
                )
            ),
        )
        try:
            await client.notify("notifications/initialized", {})
            assert stream.closed and client._session_id is None
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_response_at_collection_limit_remains_usable(monkeypatch):
    async def scenario():
        body = json.dumps({"id": 1, "result": {"ok": True}}).encode()
        monkeypatch.setattr("corki.mcp.client.MAX_HTTP_RESPONSE_BYTES", len(body))
        stream = ProbeStream([body[:3], body[3:]])
        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream)),
        )
        try:
            assert await client.request("tools/call", {}) == {"ok": True}
            assert stream.closed
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("over_limit", [False, True])
def test_gzip_is_decoded_once_and_decoded_bytes_are_limited(monkeypatch, over_limit):
    async def scenario():
        body = json.dumps({"id": 1, "result": {"text": "x" * 1000}}).encode()
        monkeypatch.setattr("corki.mcp.client.MAX_HTTP_RESPONSE_BYTES", len(body) - int(over_limit))
        stream = ProbeStream([gzip.compress(body)])
        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, headers={"content-encoding": "gzip"}, stream=stream)
            ),
        )
        try:
            if over_limit:
                with pytest.raises(MCPProtocolError, match="exceeds"):
                    await client.request("tools/call", {})
            else:
                assert await client.request("tools/call", {}) == {"text": "x" * 1000}
            assert stream.closed
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_read_failure_or_cancellation_closes_response_without_replay(cancel):
    async def scenario():
        entered = asyncio.Event()
        requests = []

        class Stream(ProbeStream):
            async def __aiter__(self):
                yield b'{"id":1,'
                entered.set()
                if cancel:
                    await asyncio.Future()
                raise httpx.ReadError("broken response")

        stream = Stream()

        def handle(request):
            requests.append(request)
            return httpx.Response(200, stream=stream)

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(handle),
        )
        task = asyncio.create_task(client.request("tools/call", {}))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            if cancel:
                task.cancel()
            with pytest.raises(asyncio.CancelledError if cancel else httpx.ReadError):
                await task
            assert stream.closed and len(requests) == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await client.aclose()

    asyncio.run(scenario())


def test_network_accepted_notification_completes_without_body_eof():
    async def scenario():
        tasks, disconnected = set(), asyncio.Event()

        async def serve(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            try:
                head = await reader.readuntil(b"\r\n\r\n")
                length = next(
                    int(line.split(b":", 1)[1])
                    for line in head.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                await reader.readexactly(length)
                writer.write(b"HTTP/1.1 202 Accepted\r\nTransfer-Encoding: chunked\r\n\r\n")
                await writer.drain()
                assert await asyncio.wait_for(reader.read(), 2) == b""
                disconnected.set()
            finally:
                writer.close()
                await writer.wait_closed()
                tasks.discard(task)

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url=f"http://127.0.0.1:{port}", timeout_seconds=0.5)
        )
        try:
            await asyncio.wait_for(client.notify("notifications/initialized", {}), 1)
            await asyncio.wait_for(disconnected.wait(), 1)
        finally:
            await client.aclose()
            server.close()
            await server.wait_closed()
            await asyncio.gather(*tasks)

    asyncio.run(scenario())
