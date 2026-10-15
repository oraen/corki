import asyncio
import json

import httpx
import pytest

from corki.config import MCPServerSettings
from corki.mcp.client import HttpMCPClient, MCPProtocolError


class Stream(httpx.AsyncByteStream):
    def __init__(self, wire, closed, failure=False):
        self.wire, self.closed, self.failure = wire, closed, failure

    async def __aiter__(self):
        yield self.wire
        if self.failure:
            raise httpx.ReadError("PRIVATE read failure")

    async def aclose(self):
        self.closed.append(self)


def sse(wire, closed, failure=False):
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        stream=Stream(wire, closed, failure),
    )


@pytest.mark.parametrize("session", [None, "session-original"])
@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("cursor", ["event-1", ""])
def test_resumes_by_get_without_replaying_post(session, failure, cursor):
    async def scenario():
        requests, closed = [], []

        def handle(request):
            requests.append(request)
            if request.method == "DELETE":
                return httpx.Response(204)
            if request.method == "POST":
                return sse(f"id: {cursor}\nretry: 0\nevent: ping\n\n".encode(), closed, failure)
            assert len(closed) == 1
            assert request.headers["last-event-id"] == cursor
            assert request.headers.get("mcp-session-id") == session
            assert request.headers["authorization"] == "Bearer static"
            assert request.headers["mcp-protocol-version"] == "2025-06-18"
            assert not request.content
            return sse(b'data: {"id":"1","result":{"content":[]}}\n\n', closed)

        client = HttpMCPClient(
            MCPServerSettings(
                "docs",
                "http",
                url="https://fixture.invalid/mcp",
                timeout_seconds=0.2,
                headers=(("authorization", "Bearer static"),),
            ),
            transport=httpx.MockTransport(handle),
        )
        client._session_id = session
        client._initialized = True
        client._protocol_version = "2025-06-18"
        try:
            assert await client.call_tool("read", {}) == {"content": []}
            assert [r.method for r in requests] == ["POST", "GET"]
            assert json.loads(requests[0].content)["params"]["_meta"] == {"progressToken": 0}
            assert client._next_progress_token == 1  # GET resumes this RPC, not a new one.
            assert len(closed) == 2
            assert requests[0].url == requests[1].url
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [False, True])
@pytest.mark.parametrize("wire", [b"data: partial", b"id: unfinished\n", b"id: bad\0id\n\n"])
def test_without_completed_event_id_never_resumes(wire, failure):
    async def scenario():
        methods, closed = [], []

        def handle(request):
            methods.append(request.method)
            return sse(wire, closed, failure)

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid", timeout_seconds=0.1),
            transport=httpx.MockTransport(handle),
        )
        try:
            with pytest.raises(MCPProtocolError):
                await client.call_tool("read", {})
            assert methods == ["POST"] and len(closed) == 1
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_cursor_updates_before_control_json_filter_and_parser_resets():
    async def scenario():
        cursors, closed = [], []

        def handle(request):
            if request.method == "POST":
                return sse(b"id: first\nretry: 0\ndata: {bad}\n\nid: unfinished\n", closed)
            cursors.append(request.headers["last-event-id"])
            if len(cursors) == 1:
                return sse(b"id: second\nretry: 0\nevent: ping\n\ndata: {", closed)
            return sse(b'\xef\xbb\xbfdata: {"id":1,"result":{"content":[]}}\r\n\r\n', closed)

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid", timeout_seconds=0.2),
            transport=httpx.MockTransport(handle),
        )
        try:
            assert await client.call_tool("read", {}) == {"content": []}
            assert cursors == ["first", "second"] and len(closed) == 3
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_initialize_does_not_use_ordinary_request_resumption():
    async def scenario():
        methods, closed = [], []

        def handle(request):
            methods.append(request.method)
            assert json.loads(request.content)["method"] == "initialize"
            return sse(b"id: init\nretry: 0\n\n", closed)

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid", timeout_seconds=0.2),
            transport=httpx.MockTransport(handle),
        )
        try:
            with pytest.raises(MCPProtocolError):
                await client.start()
            assert methods == ["POST"] and len(closed) == 1
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure,expected", [(False, [7.0, 2.0, 4.0, 1.0]), (True, [7.0, 7.0, 7.0])]
)
@pytest.mark.parametrize("get_failure", ["status", "transport"])
def test_retry_timing_and_counter_reset(monkeypatch, failure, expected, get_failure):
    async def scenario():
        delays, closed, requests = [], [], []

        async def pause(delay):
            delays.append(delay)
            await asyncio.sleep(0)

        monkeypatch.setattr("corki.mcp.sse_resume.sleep", pause)

        def handle(request):
            requests.append(request.method)
            if request.method == "POST":
                return sse(b"id: first\nretry: 7000\n\n", closed, failure)
            if requests.count("GET") <= 2:
                if get_failure == "transport":
                    raise httpx.ConnectError("PRIVATE connect failure")
                # Failed GET headers are sufficient; their body must never be read.
                return httpx.Response(503, stream=Stream(b"PRIVATE", closed))
            if requests.count("GET") == 3:
                return sse(b"id: next\n\n", closed)
            return sse(b'data: {"id":1,"result":{"content":[]}}\n\n', closed)

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid", timeout_seconds=0.2),
            transport=httpx.MockTransport(handle),
        )
        try:
            assert await client.call_tool("read", {}) == {"content": []}
            assert delays == expected
            assert requests == ["POST", "GET", "GET", "GET", "GET"]
            assert len(closed) == (5 if get_failure == "status" else 3)
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_get_json_mime_still_uses_sse_event_framing():
    async def scenario():
        closed = []

        def handle(request):
            if request.method == "POST":
                return sse(b"id: first\nretry: 0\n\n", closed)
            response = sse(b'data: {"id":1,"result":{"content":[]}}\n\n', closed)
            response.headers["content-type"] = "application/json; charset=utf-8"
            return response

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid", timeout_seconds=0.2),
            transport=httpx.MockTransport(handle),
        )
        try:
            assert await client.call_tool("read", {}) == {"content": []}
            assert len(closed) == 2
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["transport", "field", "utf8"])
def test_stream_error_with_cursor_resumes_immediately(monkeypatch, kind):
    async def scenario():
        closed, pauses = [], []

        async def pause(delay):
            pauses.append(delay)

        monkeypatch.setattr("corki.mcp.sse_resume.sleep", pause)

        def handle(request):
            if request.method == "POST":
                tail = {"transport": b"", "field": b"unknown: x\n", "utf8": b"data: \xff\n"}[kind]
                return sse(b"id: first\nretry: 9000\n\n" + tail, closed, kind == "transport")
            return sse(b'data: {"id":1,"result":{"content":[]}}\n\n', closed)

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid", timeout_seconds=0.2),
            transport=httpx.MockTransport(handle),
        )
        try:
            assert await client.call_tool("read", {}) == {"content": []}
            assert pauses == [] and len(closed) == 2
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_oversized_event_is_terminal_even_with_cursor(monkeypatch):
    async def scenario():
        methods, closed = [], []
        monkeypatch.setattr("corki.mcp.client.MAX_HTTP_RESPONSE_BYTES", 64)

        def handle(request):
            methods.append(request.method)
            return sse(b"id: first\nretry: 0\n\ndata: " + b"x" * 65, closed)

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid", timeout_seconds=0.2),
            transport=httpx.MockTransport(handle),
        )
        try:
            with pytest.raises(MCPProtocolError, match="exceeds"):
                await client.call_tool("read", {})
            assert methods == ["POST"] and len(closed) == 1
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [401, 404, 405, 503, 302, 200])
@pytest.mark.parametrize("method", ["tools/list", "tools/call"])
@pytest.mark.parametrize("deadline_stage", ["before_get", "after_rejected_get"])
def test_failed_get_deadline_does_not_reinitialize_or_replay_post(status, method, deadline_stage):
    async def scenario():
        messages, closed, gets = [], [], []

        def expire_operation():
            budgets = tuple(client.active_time._budgets)
            # MCP request budget is entered before the HTTP carrier budget.
            assert len(budgets) == 2
            budgets[0].reschedule(asyncio.get_running_loop().time() + 0.03)

        class Initial(Stream):
            async def __aiter__(self):
                if deadline_stage == "before_get":
                    expire_operation()
                    await asyncio.sleep(0.06)
                async for chunk in super().__aiter__():
                    yield chunk

        class Unread(Stream):
            async def __aiter__(self):
                pytest.fail("Rejected GET body must not be read")
                yield b""

            async def aclose(self):
                await super().aclose()
                # Expire the real operation timeout after the rejected carrier
                # is closed, independent of HTTP setup/scheduling latency.
                expire_operation()

        def handle(request):
            if request.method == "DELETE":
                return httpx.Response(204)
            if request.method == "GET":
                if "last-event-id" not in request.headers:
                    return httpx.Response(405)
                gets.append(request)
                return httpx.Response(
                    status,
                    headers={"content-type": "text/plain", "location": "https://other.invalid"},
                    stream=Unread(b"PRIVATE", closed),
                )
            message = json.loads(request.content)
            messages.append(message["method"])
            if "id" not in message:
                return httpx.Response(202)
            if message["method"] == "initialize":
                return httpx.Response(
                    200,
                    headers={"mcp-session-id": "original"},
                    json={
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "result": {
                            "capabilities": {},
                            "serverInfo": {"name": "fixture", "version": "1"},
                            "protocolVersion": "2025-06-18",
                        },
                    },
                )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=Initial(b"id: first\nretry: 0\n\n", closed),
            )

        client = HttpMCPClient(
            MCPServerSettings(
                "docs",
                "http",
                url="https://fixture.invalid",
                timeout_seconds=10,
            ),
            transport=httpx.MockTransport(handle),
        )
        try:
            await client.start()
            with pytest.raises(TimeoutError):
                await client.request(method, {})
            assert messages == ["initialize", "notifications/initialized", method]
            expected_gets = int(deadline_stage == "after_rejected_get")
            assert len(gets) == expected_gets and len(closed) == 1 + expected_gets
            assert client._session_id == "original"
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["delay", "send", "read", "close"])
def test_cancellation_owns_resumed_carrier_and_stops_future_gets(stage):
    async def scenario():
        arrived, release, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()
        methods, closed = [], []

        class Blocking(Stream):
            async def __aiter__(self):
                if stage == "read":
                    arrived.set()
                    await asyncio.Event().wait()
                yield b'data: {"id":1,"result":{"content":[]}}\n\n'

            async def aclose(self):
                if stage == "close":
                    arrived.set()
                    await release.wait()
                await super().aclose()
                cleaned.set()

        async def handle(request):
            methods.append(request.method)
            if request.method == "POST":
                delay = 10000 if stage == "delay" else 0
                response = sse(f"id: first\nretry: {delay}\n\n".encode(), closed)
                if stage == "delay":
                    arrived.set()
                return response
            if stage == "send":
                arrived.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaned.set()
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=Blocking(b"", closed)
            )

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid", timeout_seconds=10),
            transport=httpx.MockTransport(handle),
        )
        task = asyncio.create_task(client.call_tool("read", {}))
        try:
            await asyncio.wait_for(arrived.wait(), 1)
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            if stage == "close":
                assert not task.done() and not cleaned.is_set()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert methods == (["POST"] if stage == "delay" else ["POST", "GET"])
            assert len(closed) == (2 if stage in ("read", "close") else 1)
            if stage != "delay":
                assert cleaned.is_set()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await client.aclose()

    asyncio.run(scenario())


def test_get_close_transport_error_is_not_a_post_send_error():
    async def scenario():
        calls, closed = [], []

        class BrokenClose(Stream):
            async def aclose(self):
                await super().aclose()
                raise httpx.ReadError("PRIVATE close failure")

        def handle(request):
            if request.method == "GET":
                packet = json.dumps({"id": calls[-1]["id"], "result": {"tools": []}}).encode()
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    stream=BrokenClose(b"data: " + packet + b"\n\n", closed),
                )
            message = json.loads(request.content)
            if message["method"] == "tools/list":
                calls.append(message)
                return sse(b"id: first\nretry: 0\n\n", closed)
            if "id" not in message:
                return httpx.Response(202)
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {
                        "capabilities": {},
                        "serverInfo": {"name": "fixture", "version": "1"},
                        "protocolVersion": "2025-06-18",
                    },
                },
            )

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid", timeout_seconds=2),
            transport=httpx.MockTransport(handle),
        )
        try:
            await client.start()
            with pytest.raises(MCPProtocolError) as caught:
                await client.list_tools()
            assert "PRIVATE" not in str(caught.value)
            assert len(calls) == 1 and len(closed) == 2
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_concurrent_resumption_keeps_request_cursor_and_header_snapshot():
    async def scenario():
        posts, gets, closed = [], [], []
        both = asyncio.Event()

        async def handle(request):
            if request.method == "DELETE":
                return httpx.Response(204)
            if request.method == "POST":
                message = json.loads(request.content)
                posts.append(message)
                if len(posts) == 2:
                    client._session_id = "future"
                return sse(f"id: cursor-{message['id']}\nretry: 0\n\n".encode(), closed)
            gets.append(request)
            if len(gets) == 2:
                both.set()
            await both.wait()
            assert request.headers["mcp-session-id"] == "original"
            identity = int(request.headers["last-event-id"].split("-")[-1])
            right = {
                "id": identity,
                "result": {"content": [{"type": "text", "text": str(identity)}]},
            }
            # Cross-request first responses are covered by the shared-router tests.
            wire = "data: " + json.dumps(right) + "\n\n"
            return sse(wire.encode(), closed)

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid", timeout_seconds=0.3),
            transport=httpx.MockTransport(handle),
        )
        client._session_id = "original"
        try:
            results = await asyncio.gather(client.call_tool("one", {}), client.call_tool("two", {}))
            assert [r["content"][0]["text"] for r in results] == ["1", "2"]
            assert len(posts) == len(gets) == 2 and len(closed) == 4
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_client_shutdown_joins_resumed_read_and_transport():
    async def scenario():
        reading, cleaned = asyncio.Event(), asyncio.Event()
        closed, methods = [], []

        class Waiting(Stream):
            async def __aiter__(self):
                reading.set()
                await asyncio.Event().wait()
                yield b""

            async def aclose(self):
                await super().aclose()
                cleaned.set()

        def handle(request):
            methods.append(request.method)
            if request.method == "POST":
                return sse(b"id: first\nretry: 0\n\n", closed)
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=Waiting(b"", closed)
            )

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid", timeout_seconds=10),
            transport=httpx.MockTransport(handle),
        )
        task = asyncio.create_task(client.call_tool("read", {}))
        try:
            await asyncio.wait_for(reading.wait(), 1)
            await asyncio.wait_for(client.aclose(), 1)
            assert cleaned.is_set() and client.is_closed and task.cancelled()
            assert methods == ["POST", "GET"] and len(closed) == 2
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await client.aclose()

    asyncio.run(scenario())


def test_loopback_get_resumes_chunked_response_without_post_replay():
    async def scenario():
        peers, errors, requests = set(), [], []
        disconnected = asyncio.Event()

        async def serve(reader, writer):
            task = asyncio.current_task()
            peers.add(task)
            try:
                head = await reader.readuntil(b"\r\n\r\n")
                lines = head.split(b"\r\n")
                method = lines[0].split()[0]
                headers = dict(line.lower().split(b":", 1) for line in lines[1:] if b":" in line)
                body = await reader.readexactly(int(headers.get(b"content-length", b"0")))
                requests.append((method, headers, body))
                if method == b"POST":
                    packet = b"id: cursor\nretry: 0\n\n"
                else:
                    assert headers[b"last-event-id"].strip() == b"cursor" and not body
                    identity = json.loads(requests[0][2])["id"]
                    packet = (
                        b"data: "
                        + json.dumps({"id": str(identity), "result": {"content": []}}).encode()
                        + b"\n\n"
                    )
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                    b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
                )
                writer.write(f"{len(packet):x}\r\n".encode() + packet + b"\r\n")
                if method == b"POST":
                    writer.write(b"0\r\n\r\n")
                await writer.drain()
                if method == b"GET":
                    assert await reader.read() == b""
                    disconnected.set()
            except Exception as error:
                errors.append(error)
            finally:
                writer.close()
                await writer.wait_closed()
                peers.discard(task)

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url=f"http://127.0.0.1:{port}/mcp", timeout_seconds=1)
        )
        try:
            assert await client.call_tool("read", {}) == {"content": []}
            await asyncio.wait_for(disconnected.wait(), 1)
            assert [r[0] for r in requests] == [b"POST", b"GET"] and not errors
        finally:
            await client.aclose()
            server.close()
            await server.wait_closed()
            pending = tuple(peers)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    asyncio.run(scenario())
