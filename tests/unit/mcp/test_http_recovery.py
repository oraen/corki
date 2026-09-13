import asyncio
import json

import httpx
import pytest

from corki.config import MCPServerSettings
from corki.mcp.client import HttpMCPClient


class Server:
    def __init__(self, failures=(), phase="tools/call", sessions=True):
        self.failures = list(failures)
        self.phase, self.sessions = phase, sessions
        self.requests = []
        self.initializations = 0

    async def handle(self, request):
        if request.method == "DELETE":
            return httpx.Response(204)
        message = json.loads(request.content)
        self.requests.append((message, request.headers.get("mcp-session-id")))
        method = message["method"]
        if method == "initialize":
            self.initializations += 1
        if method == self.phase and self.failures:
            failure = self.failures.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return httpx.Response(failure)
        if method == "notifications/initialized":
            return httpx.Response(202)
        result = {"content": [{"type": "text", "text": "done"}]}
        headers = {}
        if method == "initialize":
            result = {
                "capabilities": {},
                "serverInfo": {"name": "fixture", "version": "1"},
                "protocolVersion": "2025-06-18",
            }
            if self.sessions:
                headers["mcp-session-id"] = str(self.initializations)
        elif method == "tools/list":
            result = {"tools": [{"name": "read", "inputSchema": {"type": "object"}}]}
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": message["id"], "result": result}, headers=headers
        )

    def client(self, timeout=5):
        return HttpMCPClient(
            MCPServerSettings(
                "docs", "http", url="https://fixture.invalid", timeout_seconds=timeout
            ),
            transport=httpx.MockTransport(self.handle),
        )


@pytest.mark.parametrize("phase", ["initialize", "tools/call"])
def test_auth_preparation_does_not_disable_actual_rpc_timeout(monkeypatch, phase):
    async def scenario():
        server = Server(sessions=False)
        original_handle = server.handle
        auth_calls = []
        blocked_requests = []

        async def prepare(client):
            auth_calls.append(client)
            await asyncio.sleep(0.1)

        async def handle(request):
            packet = json.loads(request.content)
            if packet["method"] == phase:
                blocked_requests.append(packet)
                await asyncio.Event().wait()
            return await original_handle(request)

        monkeypatch.setattr(HttpMCPClient, "_prepare_oauth", prepare)
        server.handle = handle
        client = server.client(timeout=0.05)
        try:
            with pytest.raises(TimeoutError):
                await client.start()
                if phase == "tools/call":
                    await client.call_tool("read", {})
            assert len(blocked_requests) == 1, "auth spent the RPC budget or RPC was replayed"
            assert len(auth_calls) == (1 if phase == "initialize" else 2)
        finally:
            await client.aclose()

    asyncio.run(asyncio.wait_for(scenario(), 3))


@pytest.mark.parametrize("phase", ["initialize", "tools/call"])
def test_close_joins_owned_auth_preparation_before_releasing_session(monkeypatch, phase):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        server = Server(sessions=False)
        preparing = phase == "initialize"
        finished = False

        async def prepare(client):
            nonlocal finished
            if not preparing:
                return
            entered.set()
            # Simulate the refresh transaction joining persistence after cancellation.
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
                raise
            finally:
                finished = True

        monkeypatch.setattr(HttpMCPClient, "_prepare_oauth", prepare)
        client = server.client(timeout=0.05)
        if phase == "tools/call":
            await client.start()
            preparing = True
        task = asyncio.create_task(
            client.start() if phase == "initialize" else client.call_tool("read", {})
        )
        close = None
        try:
            await asyncio.wait_for(entered.wait(), 1)
            close = asyncio.create_task(client.aclose())
            await asyncio.sleep(0.1)
            assert not close.done() and not client._client.is_closed
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            await close
            assert finished and client._client.is_closed
            assert not any(m["method"] == phase for m, _ in server.requests)
            assert not client._recovery._operations and not client._recovery._users
        finally:
            release.set()
            await asyncio.gather(task, *([close] if close else []), return_exceptions=True)
            await client.aclose()

    asyncio.run(asyncio.wait_for(scenario(), 3))


@pytest.mark.parametrize("phase", ["initialize", "notifications/initialized", "tools/list"])
def test_transient_lifecycle_failure_retries(phase):
    async def scenario():
        server = Server([502], phase)
        client = server.client()
        try:
            await client.start()
            assert len(await client.list_tools()) == 1
            assert len([m for m, _ in server.requests if m["method"] == phase]) == 2
            assert server.initializations == (1 if phase == "tools/list" else 2)
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_recovery_does_not_close_shared_http_carrier_before_owner_shutdown():
    async def scenario():
        server = Server([404])

        class Carrier(httpx.AsyncBaseTransport):
            closes = 0

            async def handle_async_request(self, request):
                assert self.closes == 0, "retiring a session closed the shared carrier"
                return await server.handle(request)

            async def aclose(self):
                self.closes += 1

        transport = Carrier()
        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"), transport=transport
        )
        try:
            await client.start()
            await client.call_tool("read", {})
            await asyncio.sleep(0.01)
            await client.call_tool("read", {})
            assert transport.closes == 0
        finally:
            await client.aclose()
        await client.aclose()
        assert transport.closes == 1

    asyncio.run(scenario())


def test_session404_rebuilds_handshake_and_retries_once():
    async def scenario():
        server = Server([404])
        client = server.client()
        try:
            await client.start()
            result = await client.call_tool("read", {"value": 7})
            assert result == {"content": [{"type": "text", "text": "done"}]}
            calls = [(m, s) for m, s in server.requests if m["method"] == "tools/call"]
            assert [s for _, s in calls] == ["1", "2"]
            assert calls[0][0]["params"] == calls[1][0]["params"]
            assert server.initializations == 2
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_second404_propagates_without_recursive_recovery():
    async def scenario():
        server = Server([404, 404])
        client = server.client()
        try:
            await client.start()
            with pytest.raises(httpx.HTTPStatusError):
                await client.call_tool("read", {})
            assert server.initializations == 2
            assert len([m for m, _ in server.requests if m["method"] == "tools/call"]) == 2
            assert await client.call_tool("read", {}) == {
                "content": [{"type": "text", "text": "done"}]
            }
            assert server.initializations == 2
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure,sessions", [(404, False), (500, True), (401, True), (httpx.ReadError("lost"), True)]
)
def test_non_session_failure_is_not_replayed(failure, sessions):
    async def scenario():
        server = Server([failure], sessions=sessions)
        client = server.client()
        try:
            await client.start()
            with pytest.raises(httpx.HTTPError):
                await client.call_tool("read", {})
            assert server.initializations == 1
            assert len([m for m, _ in server.requests if m["method"] == "tools/call"]) == 1
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_concurrent_expiry_uses_one_new_generation():
    async def scenario():
        server = Server()
        entered = asyncio.Event()
        old_calls = 0
        original = server.handle

        async def handle(request):
            nonlocal old_calls
            if request.method == "POST":
                message = json.loads(request.content)
                if (
                    message["method"] == "tools/call"
                    and request.headers.get("mcp-session-id") == "1"
                ):
                    old_calls += 1
                    if old_calls == 2:
                        entered.set()
                    await entered.wait()
                    return httpx.Response(404)
            return await original(request)

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(handle),
        )
        try:
            await client.start()
            results = await asyncio.gather(
                client.call_tool("read", {}), client.call_tool("read", {})
            )
            assert results == [{"content": [{"type": "text", "text": "done"}]}] * 2
            assert old_calls == 2 and server.initializations == 2
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["initialize", "notifications/initialized", "tools/list"])
def test_retry_exhaustion_is_three_attempts(phase):
    async def scenario():
        server = Server([503] * 4, phase)
        client = server.client()
        try:
            with pytest.raises(httpx.HTTPStatusError):
                await client.start()
                await client.list_tools()
            assert len([m for m, _ in server.requests if m["method"] == phase]) == 3
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["initialize", "tools/list"])
def test_retry_backoff_shares_one_deadline(phase):
    async def scenario():
        server = Server([502] * 3, phase)
        client = server.client(timeout=0.05)
        try:
            with pytest.raises(TimeoutError):
                await client.start()
                await client.list_tools()
            assert len([m for m, _ in server.requests if m["method"] == phase]) == 1
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["cancel", "close"])
def test_recovery_cancel_or_close_does_not_publish_partial_generation(action):
    async def scenario():
        server = Server([404])
        entered, cleaned = asyncio.Event(), asyncio.Event()
        original = server.handle

        async def handle(request):
            if request.method == "POST":
                message = json.loads(request.content)
                if message["method"] == "initialize" and server.initializations == 1:
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cleaned.set()
            return await original(request)

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(handle),
        )
        await client.start()
        old = client._recovery.current
        task = asyncio.create_task(client.call_tool("read", {}))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            if action == "cancel":
                task.cancel()
            else:
                await client.aclose()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert cleaned.is_set() and client._recovery.current is old
            assert not client._recovery._users
            assert len([m for m, _ in server.requests if m["method"] == "tools/call"]) == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await client.aclose()

    asyncio.run(scenario())


def test_recovery_handshake_retries_and_preserves_static_headers_with_fresh_helper(
    monkeypatch, tmp_path
):
    async def scenario():
        server = Server([404])
        original = server.handle
        initializes, helpers, seen = 0, [], []

        async def helper(command, cwd):
            helpers.append((command, cwd))
            return httpx.Headers({"x-helper": str(len(helpers))})

        async def handle(request):
            nonlocal initializes
            if request.method == "POST":
                method = json.loads(request.content)["method"]
                seen.append((method, request.headers["x-env"], request.headers["x-helper"]))
                if method == "initialize":
                    initializes += 1
                    if initializes == 2:
                        return httpx.Response(502)
            return await original(request)

        monkeypatch.setenv("CORKI_RECOVERY_HEADER_TEST", "original")
        monkeypatch.setattr("corki.mcp.header_helper.run_helper", helper)
        client = HttpMCPClient(
            MCPServerSettings(
                "docs",
                "http",
                url="https://fixture.invalid",
                cwd=tmp_path,
                env_http_headers=(("x-env", "CORKI_RECOVERY_HEADER_TEST"),),
                http_headers_helper="fixture-helper",
            ),
            transport=httpx.MockTransport(handle),
        )
        try:
            await client.start()
            monkeypatch.setenv("CORKI_RECOVERY_HEADER_TEST", "changed")
            await client.call_tool("read", {})
            assert initializes == 3 and len(helpers) == 3
            assert all(value == "original" for _, value, _ in seen)
            assert [helper for method, _, helper in seen if method == "initialize"] == [
                "1",
                "2",
                "3",
            ]
            assert [helper for method, _, helper in seen if method == "tools/call"] == ["1", "3"]
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_session404_headers_skip_body_and_join_close():
    async def scenario():
        server = Server()
        original = server.handle
        closing, release = asyncio.Event(), asyncio.Event()

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                raise AssertionError("404 session expiry body must not be read")
                yield b""

            async def aclose(self):
                closing.set()
                await release.wait()

        async def handle(request):
            if (
                request.method == "POST"
                and request.headers.get("mcp-session-id") == "1"
                and json.loads(request.content)["method"] == "tools/call"
            ):
                return httpx.Response(404, stream=Stream())
            return await original(request)

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(handle),
        )
        await client.start()
        task = asyncio.create_task(client.call_tool("read", {}))
        try:
            await asyncio.wait_for(closing.wait(), 2)
            assert not task.done() and server.initializations == 1
            release.set()
            await task
            assert server.initializations == 2
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await client.aclose()

    asyncio.run(scenario())


def test_old_successful_call_keeps_generation_and_cannot_replace_new_session():
    async def scenario():
        server = Server()
        original = server.handle
        entered, release = asyncio.Event(), asyncio.Event()
        deleted = []

        async def handle(request):
            if request.method == "DELETE":
                deleted.append(request.headers["mcp-session-id"])
            else:
                message = json.loads(request.content)
                if (
                    message["method"] == "tools/call"
                    and request.headers.get("mcp-session-id") == "1"
                ):
                    if message["params"]["name"] == "slow":
                        entered.set()
                        await release.wait()
                        return httpx.Response(
                            200,
                            json={"jsonrpc": "2.0", "id": message["id"], "result": {"content": []}},
                            headers={"mcp-session-id": "late-old"},
                        )
                    return httpx.Response(404)
            return await original(request)

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(handle),
        )
        await client.start()
        old = client._recovery.current
        task = asyncio.create_task(client.call_tool("slow", {}))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await client.call_tool("read", {})
            assert not old._client.is_closed and not deleted
            release.set()
            assert await task == {"content": []}
            await client.call_tool("read", {})
            assert client._recovery.current._session_id == "2"
            assert server.initializations == 2
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await client.aclose()
        assert set(deleted) == {"late-old", "2"} and old._client.is_closed

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [400, 401, 403, 404, 501])
def test_non_transient_handshake_status_is_not_retried(status):
    async def scenario():
        server = Server([status], "initialize")
        client = server.client()
        try:
            with pytest.raises(httpx.HTTPStatusError):
                await client.start()
            assert server.initializations == 1
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["resources/read", "prompts/get", "tools/call"])
def test_transient_status_does_not_retry_non_tools_list_operation(method):
    async def scenario():
        server = Server([502], method)
        client = server.client()
        try:
            await client.start()
            with pytest.raises(httpx.HTTPStatusError):
                await client.request(method, {})
            assert len([m for m, _ in server.requests if m["method"] == method]) == 1
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_literal_session_header_is_not_negotiated_session_authority():
    async def scenario():
        server = Server([404], sessions=False)
        client = HttpMCPClient(
            MCPServerSettings(
                "docs",
                "http",
                url="https://fixture.invalid",
                http_headers=(("mcp-session-id", "literal"),),
            ),
            transport=httpx.MockTransport(server.handle),
        )
        try:
            await client.start()
            with pytest.raises(httpx.HTTPStatusError):
                await client.call_tool("read", {})
            assert server.initializations == 1
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_loopback_expiry_recovers_without_waiting_for_404_body_eof():
    async def scenario():
        peers, errors, calls = set(), [], []
        initializations = 0
        expired_closed = asyncio.Event()

        async def serve(reader, writer):
            nonlocal initializations
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
                body = await reader.readexactly(int(headers.get(b"content-length", b"0")))
                session_header = b""
                if lines[0].startswith(b"DELETE"):
                    status, payload = b"204 No Content", b""
                elif lines[0].startswith(b"GET"):
                    status, payload = b"405 Method Not Allowed", b""
                else:
                    message = json.loads(body)
                    method = message["method"]
                    if method == "tools/call":
                        calls.append((message["params"], headers.get(b"mcp-session-id")))
                        if len(calls) == 1:
                            writer.write(
                                b"HTTP/1.1 404 Not Found\r\nTransfer-Encoding: chunked\r\n\r\n"
                            )
                            await writer.drain()
                            assert await asyncio.wait_for(reader.read(), 2) == b""
                            expired_closed.set()
                            return
                    if method == "notifications/initialized":
                        status, payload = b"202 Accepted", b""
                    else:
                        result = {"content": []}
                        if method == "initialize":
                            initializations += 1
                            session_header = f"Mcp-Session-Id: {initializations}\r\n".encode()
                            result = {
                                "capabilities": {},
                                "serverInfo": {"name": "fixture", "version": "1"},
                                "protocolVersion": "2025-06-18",
                            }
                        status = b"200 OK"
                        payload = json.dumps(
                            {"jsonrpc": "2.0", "id": message["id"], "result": result}
                        ).encode()
                writer.write(
                    b"HTTP/1.1 "
                    + status
                    + b"\r\nContent-Type: application/json\r\n"
                    + session_header
                    + f"Content-Length: {len(payload)}\r\n".encode()
                    + b"Connection: close\r\n\r\n"
                    + payload
                )
                await writer.drain()
            except BaseException as error:
                errors.append(error)
            finally:
                writer.close()
                await writer.wait_closed()
                peers.discard(task)

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        client = HttpMCPClient(
            MCPServerSettings(
                "docs",
                "http",
                url=f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}",
                timeout_seconds=1,
            )
        )
        try:
            await client.start()
            assert await client.call_tool("read", {"key": 7}) == {"content": []}
            await asyncio.wait_for(expired_closed.wait(), 2)
            assert calls == [
                ({"name": "read", "arguments": {"key": 7}, "_meta": {"progressToken": 0}}, b"1"),
                ({"name": "read", "arguments": {"key": 7}, "_meta": {"progressToken": 0}}, b"2"),
            ]
            assert initializations == 2
        finally:
            await client.aclose()
            server.close()
            await server.wait_closed()
            await asyncio.gather(*peers)
        assert errors == []

    asyncio.run(scenario())


def test_notification_keeps_its_old_generation_during_recovery():
    async def scenario():
        server = Server([404])
        entered, release = asyncio.Event(), asyncio.Event()
        original = server.handle

        async def handle(request):
            if (
                request.method == "POST"
                and json.loads(request.content)["method"] == "notifications/test"
            ):
                entered.set()
                await release.wait()
                return httpx.Response(202)
            return await original(request)

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(handle),
        )
        await client.start()
        old = client._recovery.current
        notification = asyncio.create_task(client.notify("notifications/test", {}))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await client.call_tool("read", {})
            await asyncio.sleep(0.01)
            assert not old._client.is_closed
            release.set()
            await notification
        finally:
            release.set()
            await asyncio.gather(notification, return_exceptions=True)
            await client.aclose()
        assert old._client.is_closed

    asyncio.run(scenario())
