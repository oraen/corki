import asyncio
import json

import httpx
import pytest

from corki.config import MCPServerSettings
from corki.mcp.client import HttpMCPClient, MCPProtocolError


def packet(identity, result=None, *, error=False):
    return {
        "jsonrpc": "2.0",
        "id": identity,
        **(
            {"error": {"code": -32001, "message": "remote failure"}}
            if error
            else {"result": result}
        ),
    }


class QueueStream(httpx.AsyncByteStream):
    def __init__(self):
        self.queue = asyncio.Queue()
        self.closed = asyncio.Event()

    async def __aiter__(self):
        while True:
            value = await self.queue.get()
            if value is None:
                return
            yield value

    async def aclose(self):
        self.closed.set()

    def emit(self, value):
        self.queue.put_nowait(b"data: " + json.dumps(value).encode() + b"\n\n")


class Server:
    def __init__(self, *, session=True, get_status=200):
        self.session, self.get_status = session, get_status
        self.stream = QueueStream()
        self.requests, self.calls = [], []
        self.get_started = asyncio.Event()
        self.two_calls = asyncio.Event()
        self.initialized = False
        self.deleted = False

    async def handle(self, request):
        self.requests.append(request)
        if request.method == "DELETE":
            assert not self.get_started.is_set() or self.stream.closed.is_set()
            self.deleted = True
            return httpx.Response(204)
        if request.method == "GET":
            assert self.initialized
            assert request.headers["mcp-session-id"] == "session-one"
            assert request.headers["mcp-protocol-version"] == "2025-06-18"
            assert request.headers["authorization"] == "Bearer explicit"
            assert request.headers["accept"] == "text/event-stream, application/json"
            self.get_started.set()
            return httpx.Response(
                self.get_status,
                headers={"content-type": "text/event-stream"},
                stream=self.stream,
            )
        message = json.loads(request.content)
        if message["method"] == "initialize":
            return httpx.Response(
                200,
                headers={"mcp-session-id": "session-one"} if self.session else {},
                json=packet(
                    message["id"],
                    {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "fixture", "version": "1"},
                    },
                ),
            )
        if message["method"] == "notifications/initialized":
            self.initialized = True
            return httpx.Response(202)
        self.calls.append(message)
        if len(self.calls) == 2:
            self.two_calls.set()
        return await self.call(message)

    async def call(self, message):
        return httpx.Response(202)

    def client(self, *, timeout=0.2):
        return HttpMCPClient(
            MCPServerSettings(
                "fixture",
                "http",
                url="https://fixture.invalid/mcp",
                headers=(("authorization", "Bearer explicit"),),
                timeout_seconds=timeout,
            ),
            transport=httpx.MockTransport(self.handle),
        )


@pytest.mark.parametrize("error", [False, True])
@pytest.mark.parametrize("status", [202, 204])
def test_accepted_post_receives_common_stream_result_without_replay(error, status):
    async def scenario():
        server = Server()

        async def call(message):
            server.stream.emit(packet("+000" + str(message["id"]), {"value": 7}, error=error))
            return httpx.Response(status)

        server.call = call
        client = server.client()
        try:
            await client.start()
            if error:
                with pytest.raises(MCPProtocolError, match="remote failure"):
                    await client.request("tools/call", {})
            else:
                assert await client.request("tools/call", {}) == {"value": 7}
            assert len(server.calls) == 1
            assert server.get_started.is_set() and not server.stream.closed.is_set()
        finally:
            await client.aclose()
        assert server.stream.closed.is_set() and server.deleted

    asyncio.run(scenario())


def test_common_stream_reverse_concurrent_responses_duplicates_and_unknown_ids():
    async def scenario():
        server = Server()

        async def call(message):
            await server.two_calls.wait()
            if message is server.calls[-1]:
                server.stream.emit(packet(9999, "unknown"))
                for item in reversed(server.calls):
                    server.stream.emit(packet(item["id"], item["params"]["name"]))
                    server.stream.emit(packet(item["id"], "duplicate"))
            return httpx.Response(202)

        server.call = call
        client = server.client()
        try:
            await client.start()
            results = await asyncio.gather(
                client.request("tools/call", {"name": "a"}),
                client.request("tools/call", {"name": "b"}),
            )
            assert results == ["a", "b"] and len(server.calls) == 2
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("get_status", [405, 404, 401, 500])
def test_initial_get_failure_isolated_and_accepted_call_uses_original_deadline(get_status):
    async def scenario():
        server = Server(get_status=get_status)
        client = server.client(timeout=0.06)
        try:
            await client.start()
            with pytest.raises(TimeoutError):
                await client.request("tools/call", {})
            assert len(server.calls) == 1
            assert sum(r.method == "GET" for r in server.requests) == 1
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_stateless_cross_post_json_routes_by_id():
    async def scenario():
        server = Server(session=False)

        async def call(message):
            await server.two_calls.wait()
            other = next(item for item in server.calls if item is not message)
            return httpx.Response(200, json=packet(other["id"], other["params"]["name"]))

        server.call = call
        client = server.client()
        try:
            await client.start()
            assert await asyncio.gather(
                client.request("tools/call", {"name": "a"}),
                client.request("tools/call", {"name": "b"}),
            ) == ["a", "b"]
            assert all(r.method == "POST" for r in server.requests)
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_cancelled_call_cannot_consume_another_response_or_stop_common_receiver():
    async def scenario():
        server = Server()
        client = server.client(timeout=1)
        try:
            await client.start()
            first = asyncio.create_task(client.request("tools/call", {"name": "cancel"}))
            second = asyncio.create_task(client.request("tools/call", {"name": "keep"}))
            await asyncio.wait_for(server.two_calls.wait(), 0.3)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            for call in server.calls:
                server.stream.emit(packet(call["id"], call["params"]["name"]))
            assert await second == "keep"
            assert len(server.calls) == 2 and not server.stream.closed.is_set()
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("cursor", [None, "", "checkpoint"])
@pytest.mark.parametrize("failure", ["eof", "read"])
def test_common_stream_resumes_even_without_cursor(cursor, failure):
    async def scenario():
        server = Server()
        original = server.handle
        second_get = asyncio.Event()
        carriers = []

        class Ended(httpx.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                yield (
                    "retry: 0\n" + (f"id: {cursor}\n" if cursor is not None else "") + "\n"
                ).encode()
                if failure == "read":
                    raise httpx.ReadError("PRIVATE read failure")

            async def aclose(self):
                self.closed = True

        async def handle(request):
            if request.method != "GET":
                return await original(request)
            server.requests.append(request)
            if not carriers:
                carrier = Ended()
                carriers.append(carrier)
                return httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, stream=carrier
                )
            assert carriers[0].closed
            assert request.headers.get("last-event-id") == cursor
            assert request.headers["mcp-session-id"] == "session-one"
            second_get.set()
            return httpx.Response(
                200, headers={"content-type": "application/json"}, stream=server.stream
            )

        server.handle = handle

        async def call(message):
            await second_get.wait()
            server.stream.emit(packet(message["id"], "resumed"))
            return httpx.Response(202)

        server.call = call
        client = server.client()
        try:
            await client.start()
            assert await client.request("tools/call", {}) == "resumed"
            assert len(server.calls) == 1
            assert sum(r.method == "GET" for r in server.requests) == 2
        finally:
            await client.aclose()
        assert server.stream.closed.is_set()

    asyncio.run(scenario())


def test_response_can_arrive_before_post_finishes_and_close_joins_outstanding_send():
    async def scenario():
        server = Server()
        send_closed = asyncio.Event()

        async def call(message):
            server.stream.emit(packet(message["id"], "early result"))
            try:
                await asyncio.Future()
            finally:
                send_closed.set()

        server.call = call
        client = server.client()
        try:
            await client.start()
            assert await client.request("tools/call", {}) == "early result"
            assert not send_closed.is_set()
        finally:
            await client.aclose()
        assert send_closed.is_set() and server.deleted and len(server.calls) == 1

    asyncio.run(scenario())


def test_request_sse_routes_first_response_to_another_call_and_fails_own_pending_call():
    async def scenario():
        server = Server(session=False)

        async def call(message):
            await server.two_calls.wait()
            if message["params"]["name"] == "b":
                return httpx.Response(202)
            other = next(c for c in server.calls if c["params"]["name"] == "b")
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text="data: " + json.dumps(packet(other["id"], "b result")) + "\n\n",
            )

        server.call = call
        client = server.client()
        try:
            await client.start()
            a, b = await asyncio.gather(
                client.request("tools/call", {"name": "a"}),
                client.request("tools/call", {"name": "b"}),
                return_exceptions=True,
            )
            assert isinstance(a, MCPProtocolError) and "closed before" in str(a)
            assert b == "b result" and len(server.calls) == 2
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_recovery_receivers_and_numeric_ids_belong_to_their_exact_generation():
    async def scenario():
        streams = {"1": QueueStream(), "2": QueueStream()}
        initializations = 0
        calls, deleted = [], []
        old_waiting = asyncio.Event()

        async def handle(request):
            nonlocal initializations
            session = request.headers.get("mcp-session-id")
            if request.method == "GET":
                return httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, stream=streams[session]
                )
            if request.method == "DELETE":
                assert streams[session].closed.is_set()
                deleted.append(session)
                return httpx.Response(204)
            message = json.loads(request.content)
            if message["method"] == "initialize":
                initializations += 1
                return httpx.Response(
                    200,
                    headers={"mcp-session-id": str(initializations)},
                    json=packet(
                        message["id"],
                        {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "serverInfo": {"name": "fixture", "version": "1"},
                        },
                    ),
                )
            if message["method"] == "notifications/initialized":
                return httpx.Response(202)
            calls.append((session, message))
            if message["params"]["name"] == "old":
                old_waiting.set()
            elif session == "1":
                return httpx.Response(404)
            else:
                streams[session].emit(packet(message["id"], "new"))
            return httpx.Response(202)

        client = HttpMCPClient(
            MCPServerSettings("fixture", "http", url="https://fixture.invalid", timeout_seconds=1),
            transport=httpx.MockTransport(handle),
        )
        try:
            await client.start()
            old = asyncio.create_task(client.request("tools/call", {"name": "old"}))
            await asyncio.wait_for(old_waiting.wait(), 0.3)
            assert await client.request("tools/call", {"name": "recover"}) == "new"
            assert not old.done() and not streams["1"].closed.is_set()
            assert calls[0][1]["id"] == calls[-1][1]["id"] == 2
            streams["1"].emit(packet(2, "old"))
            assert await old == "old"
            assert len(calls) == 3 and initializations == 2
        finally:
            await client.aclose()
        assert sorted(deleted) == ["1", "2"]
        assert all(s.closed.is_set() for s in streams.values())

    asyncio.run(scenario())


def test_repeated_shutdown_cancellation_joins_common_stream_close_before_delete():
    async def scenario():
        server = Server()
        closing, release = asyncio.Event(), asyncio.Event()

        class Slow(QueueStream):
            async def aclose(self):
                closing.set()
                await release.wait()
                await super().aclose()

        server.stream = Slow()
        client = server.client()
        await client.start()
        await asyncio.wait_for(server.get_started.wait(), 0.3)
        task = asyncio.create_task(client.aclose())
        try:
            await asyncio.wait_for(closing.wait(), 0.3)
            for _ in range(3):
                task.cancel()
                await asyncio.sleep(0)
            assert not task.done() and not server.deleted
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert server.stream.closed.is_set() and server.deleted
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await client.aclose()

    asyncio.run(scenario())


def test_real_http_accepted_response_uses_persistent_get_and_closes_socket():
    async def scenario():
        deliveries = asyncio.Queue()
        peers, methods, errors = set(), [], []
        get_closed = asyncio.Event()

        async def serve(reader, writer):
            task = asyncio.current_task()
            peers.add(task)
            try:
                head = await reader.readuntil(b"\r\n\r\n")
                lines = head.split(b"\r\n")
                headers = {
                    k.lower(): v.strip()
                    for line in lines[1:]
                    if b":" in line
                    for k, v in [line.split(b":", 1)]
                }
                if lines[0].startswith(b"GET"):
                    assert headers[b"mcp-session-id"] == b"fixture"
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                        b"Transfer-Encoding: chunked\r\n\r\n"
                    )
                    await writer.drain()
                    disconnected = asyncio.create_task(reader.read())
                    try:
                        while True:
                            next_value = asyncio.create_task(deliveries.get())
                            try:
                                done, _ = await asyncio.wait(
                                    (next_value, disconnected), return_when=asyncio.FIRST_COMPLETED
                                )
                                if disconnected in done:
                                    get_closed.set()
                                    return
                                payload = (
                                    b"data: " + json.dumps(next_value.result()).encode() + b"\n\n"
                                )
                                writer.write(f"{len(payload):x}\r\n".encode() + payload + b"\r\n")
                                await writer.drain()
                            finally:
                                next_value.cancel()
                                await asyncio.gather(next_value, return_exceptions=True)
                    finally:
                        disconnected.cancel()
                        await asyncio.gather(disconnected, return_exceptions=True)
                body = await reader.readexactly(int(headers.get(b"content-length", b"0")))
                message = json.loads(body) if body else {"method": "DELETE"}
                method = message["method"]
                methods.append(method)
                payload, status = b"", 202
                if method == "initialize":
                    status = 200
                    payload = json.dumps(
                        packet(
                            message["id"],
                            {
                                "protocolVersion": "2025-06-18",
                                "capabilities": {},
                                "serverInfo": {"name": "fixture", "version": "1"},
                            },
                        )
                    ).encode()
                elif method == "tools/call":
                    deliveries.put_nowait(
                        packet(
                            message["id"], {"content": [{"type": "text", "text": "network result"}]}
                        )
                    )
                writer.write(
                    f"HTTP/1.1 {status} Fixture\r\nContent-Type: application/json\r\n"
                    f"Mcp-Session-Id: fixture\r\nContent-Length: {len(payload)}\r\n"
                    "Connection: close\r\n\r\n".encode()
                    + payload
                )
                await writer.drain()
            except Exception as error:
                errors.append(error)
            finally:
                writer.close()
                await writer.wait_closed()
                peers.discard(task)

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        client = HttpMCPClient(
            MCPServerSettings(
                "fixture",
                "http",
                url=f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}",
                timeout_seconds=1,
            )
        )
        try:
            await client.start()
            assert await client.call_tool("read", {}) == {
                "content": [{"type": "text", "text": "network result"}]
            }
            assert not get_closed.is_set()
        finally:
            await client.aclose()
            server.close()
            await server.wait_closed()
            await asyncio.wait_for(asyncio.gather(*tuple(peers)), 2)
        assert get_closed.is_set() and errors == []
        assert methods == ["initialize", "notifications/initialized", "tools/call", "DELETE"]

    asyncio.run(scenario())


@pytest.mark.parametrize("wire", ["json", "sse"])
def test_notification_post_response_can_complete_a_pending_rpc(wire):
    async def scenario():
        server = Server()
        waiting = asyncio.Event()

        async def call(message):
            if message["method"] == "tools/call":
                waiting.set()
                return httpx.Response(202)
            response = packet(server.calls[0]["id"], "notification-carried result")
            if wire == "json":
                return httpx.Response(200, json=response)
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text="data: " + json.dumps(response) + "\n\n",
            )

        server.call = call
        client = server.client(timeout=1)
        try:
            await client.start()
            task = asyncio.create_task(client.request("tools/call", {}))
            await asyncio.wait_for(waiting.wait(), 0.3)
            await client.notify("notifications/roots/list_changed", {})
            assert await task == "notification-carried result"
        finally:
            await client.aclose()

    asyncio.run(scenario())
