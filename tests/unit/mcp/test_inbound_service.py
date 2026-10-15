import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

from corki.config import MCPServerSettings
from corki.mcp.client import HttpMCPClient, MCPProtocolError, StdioMCPClient

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "inbound_mcp_server.py"


class Stream(httpx.AsyncByteStream):
    def __init__(self):
        self.queue = asyncio.Queue()
        self.closed = False

    def emit(self, message):
        self.queue.put_nowait(b"data: " + json.dumps(message).encode() + b"\n\n")

    async def __aiter__(self):
        while True:
            yield await self.queue.get()

    async def aclose(self):
        self.closed = True


class Server:
    def __init__(self, wire, incoming):
        self.wire, self.incoming = wire, incoming
        self.stream = Stream()
        self.calls, self.replies = [], []

    def handle(self, request):
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.method == "GET":
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=self.stream
            )
        message = json.loads(request.content)
        method = message.get("method")
        if method is None:
            self.replies.append(message)
            result = {
                "jsonrpc": "2.0",
                "id": self.calls[-1]["id"],
                "result": {"content": [{"type": "text", "text": "reverse RPC completed"}]},
            }
            if self.wire == "json":
                return httpx.Response(200, json=result)
            self.stream.emit(result)
            return httpx.Response(202)
        if method == "initialize":
            return httpx.Response(
                200,
                headers={"mcp-session-id": "session"} if self.wire == "get" else {},
                json={
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "fixture", "version": "1"},
                    },
                },
            )
        if method == "notifications/initialized":
            return httpx.Response(202)
        assert method == "tools/call"
        self.calls.append(message)
        inbound = self.incoming(message["id"])
        if self.wire == "json":
            return httpx.Response(200, json=inbound)
        for value in inbound if isinstance(inbound, list) else [inbound]:
            self.stream.emit(value)
        return (
            httpx.Response(202)
            if self.wire == "get"
            else httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=self.stream
            )
        )

    def client(self):
        return HttpMCPClient(
            MCPServerSettings(
                "fixture", "http", url="https://fixture.invalid", timeout_seconds=0.15
            ),
            transport=httpx.MockTransport(self.handle),
        )


@pytest.mark.parametrize("wire", ["json", "post_sse", "get", "stdio"])
@pytest.mark.parametrize(
    "method", ["ping", "roots/list", "sampling/createMessage", "unknown/method"]
)
def test_server_request_is_replied_without_consuming_same_id_outbound_rpc(wire, method):
    async def scenario():
        server = Server(
            wire,
            lambda identity: {"jsonrpc": "2.0", "id": identity, "method": method, "params": {}},
        )
        client = (
            StdioMCPClient(
                MCPServerSettings(
                    "fixture",
                    "stdio",
                    command=sys.executable,
                    args=("-u", str(FIXTURE)),
                    timeout_seconds=0.3,
                )
            )
            if wire == "stdio"
            else server.client()
        )
        try:
            await client.start()
            assert await client.call_tool(method, {}) == {
                "content": [{"type": "text", "text": "reverse RPC completed"}]
            }
            if wire != "stdio":
                expected = (
                    {"result": {}}
                    if method == "ping"
                    else {"result": {"roots": []}}
                    if method == "roots/list"
                    else {"error": {"code": -32601, "message": method}}
                )
                assert server.replies == [
                    {"jsonrpc": "2.0", "id": server.calls[0]["id"], **expected}
                ]
                assert len(server.calls) == 1
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("wire", ["json", "post_sse", "get", "stdio"])
def test_remote_cancellation_is_rpc_error_not_local_cancel_control_flow(wire):
    async def scenario():
        server = Server(
            wire,
            lambda identity: {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": identity, "reason": "server stopped"},
            },
        )
        client = (
            StdioMCPClient(
                MCPServerSettings(
                    "fixture",
                    "stdio",
                    command=sys.executable,
                    args=("-u", str(FIXTURE)),
                    timeout_seconds=0.3,
                )
            )
            if wire == "stdio"
            else server.client()
        )
        try:
            await client.start()
            with pytest.raises(MCPProtocolError, match="server stopped"):
                await client.call_tool("cancel", {})
            if wire != "stdio":
                assert len(server.calls) == 1 and server.replies == []
            assert not client.is_closed
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "case",
    [
        "numeric_string",
        "bool",
        "float",
        "unknown",
        "absent",
        "reason_type",
        "meta_type",
        "match",
        "null_reason",
    ],
)
def test_cancellation_identity_and_typed_params_do_not_coerce_or_overwrite(case):
    async def scenario():
        def incoming(identity):
            params = {"requestId": identity, "reason": "server stopped"}
            changes = {
                "numeric_string": {"requestId": str(identity)},
                "bool": {"requestId": True},
                "float": {"requestId": float(identity)},
                "unknown": {"requestId": 999},
                "absent": {"requestId": None},
                "reason_type": {"reason": 4},
                "meta_type": {"_meta": []},
                "match": {},
                "null_reason": {"reason": None},
            }
            params.update(changes[case])
            return [
                {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": params},
                {"jsonrpc": "2.0", "id": identity, "result": {"content": []}},
            ]

        server = Server("post_sse", incoming)
        client = server.client()
        try:
            # First outbound ID1 deliberately exercises Python's True==1 alias trap.
            if case in ("match", "null_reason"):
                with pytest.raises(MCPProtocolError) as caught:
                    await client.call_tool("read", {})
                assert type(caught.value).__name__ == "MCPRemoteCancelled"
                assert ("<unknown>" if case == "null_reason" else "server stopped") in str(
                    caught.value
                )
            else:
                assert await client.call_tool("read", {}) == {"content": []}
            assert len(server.calls) == 1 and server.replies == []
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "level,expected",
    [
        ("emergency", 40),
        ("alert", 40),
        ("critical", 40),
        ("error", 40),
        ("warning", 30),
        ("notice", 20),
        ("info", 20),
        ("debug", 10),
    ],
)
def test_remote_logging_preserves_severity_and_does_not_inject_private_meta(
    level, expected, caplog
):
    async def scenario():
        def incoming(identity):
            return [
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/message",
                    "params": {
                        "level": level,
                        "logger": "fixture category",
                        "data": "fixture message",
                        "_meta": {"private": "PRIVATE"},
                    },
                },
                {"jsonrpc": "2.0", "id": identity, "result": {"content": []}},
            ]

        server = Server("post_sse", incoming)
        client = server.client()
        try:
            assert await client.call_tool("read", {}) == {"content": []}
            assert server.replies == [] and len(server.calls) == 1
        finally:
            await client.aclose()

    caplog.set_level(10, logger="corki.mcp.inbound")
    asyncio.run(scenario())
    records = [r for r in caplog.records if r.name == "corki.mcp.inbound"]
    assert len(records) == 1 and records[0].levelno == expected
    assert "fixture message" in records[0].message and "fixture category" in records[0].message
    assert "PRIVATE" not in caplog.text


@pytest.mark.parametrize("progress,progress_logged", [(1, True), (0.5, False)])
def test_progress_and_list_notifications_are_logged_without_catalog_relisting(
    caplog, progress, progress_logged
):
    async def scenario():
        def incoming(identity):
            return [
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/progress",
                    "params": {
                        "progressToken": "token",
                        "progress": progress,
                        "total": 4,
                        "message": "working",
                    },
                },
                *(
                    {"jsonrpc": "2.0", "method": f"notifications/{kind}/list_changed"}
                    for kind in ("tools", "resources", "prompts")
                ),
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/resources/updated",
                    "params": {"uri": "fixture://resource"},
                },
                {"jsonrpc": "2.0", "id": identity, "result": {"content": []}},
            ]

        server = Server("post_sse", incoming)
        client = server.client()
        try:
            assert await client.call_tool("read", {}) == {"content": []}
            assert len(server.calls) == 1 and server.replies == []
        finally:
            await client.aclose()

    caplog.set_level(20, logger="corki.mcp.inbound")
    asyncio.run(scenario())
    records = [r for r in caplog.records if r.name == "corki.mcp.inbound"]
    # Pinned serde arbitrary_precision buffers a decimal token as a map;
    # WithMeta's flattened typed f64 cannot consume it. That notification falls
    # back to CustomNotification, not LoggingClientHandler.on_progress.
    assert len(records) == 4 + progress_logged
    if progress_logged:
        assert (
            "token" in records[0].message
            and "working" in records[0].message
            and "4" in records[0].message
        )


@pytest.mark.parametrize("wire", ["json", "post_sse", "get"])
@pytest.mark.parametrize("identity", ["server-id", "2", "+2", -1, 9223372036854775807])
def test_reverse_rpc_id_is_echoed_exactly_without_response_fallback(wire, identity):
    async def scenario():
        server = Server(
            wire,
            lambda _: {
                "jsonrpc": "2.0",
                "id": identity,
                "method": "ping",
                "params": {"_meta": {"private": "PRIVATE"}},
                "result": {"content": [{"type": "text", "text": "forged outbound result"}]},
            },
        )
        client = server.client()
        try:
            await client.start()
            assert await client.call_tool("read", {}) == {
                "content": [{"type": "text", "text": "reverse RPC completed"}]
            }
            assert server.replies == [{"jsonrpc": "2.0", "id": identity, "result": {}}]
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [400, 401, 404, 503])
def test_reverse_reply_failure_never_reinitializes_or_replays_original_call(status, caplog):
    async def scenario():
        server = Server(
            "get", lambda identity: {"jsonrpc": "2.0", "id": identity, "method": "ping"}
        )
        original = server.handle
        methods = []

        def handle(request):
            if request.method == "POST":
                message = json.loads(request.content)
                methods.append(message.get("method", "reply"))
                if "method" not in message:
                    server.replies.append(message)
                    return httpx.Response(status, text="PRIVATE")
            return original(request)

        server.handle = handle
        client = server.client()
        try:
            await client.start()
            with pytest.raises(TimeoutError):
                await client.call_tool("read", {})
            assert methods == ["initialize", "notifications/initialized", "tools/call", "reply"]
            assert len(server.calls) == len(server.replies) == 1
        finally:
            await client.aclose()

    asyncio.run(scenario())
    assert "MCP inbound reply failed" in caplog.text and "PRIVATE" not in caplog.text


def test_shutdown_joins_blocked_reverse_reply_close_under_repeated_cancellation():
    async def scenario():
        reading, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        server = Server(
            "get", lambda identity: {"jsonrpc": "2.0", "id": identity, "method": "ping"}
        )
        original = server.handle
        closed, deleted = [], []

        class ReplyStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                reading.set()
                await asyncio.Future()
                yield b""

            async def aclose(self):
                closing.set()
                await release.wait()
                closed.append(True)

        def handle(request):
            if request.method == "POST" and "method" not in json.loads(request.content):
                return httpx.Response(
                    200, headers={"content-type": "application/json"}, stream=ReplyStream()
                )
            if request.method == "DELETE":
                assert closed == [True] and server.stream.closed
                deleted.append(True)
            return original(request)

        server.handle = handle
        client = server.client()
        await client.start()
        call = asyncio.create_task(client.call_tool("read", {}))
        shutdown = None
        try:
            await asyncio.wait_for(reading.wait(), 0.3)
            shutdown = asyncio.create_task(client.aclose())
            await asyncio.wait_for(closing.wait(), 0.3)
            for _ in range(3):
                shutdown.cancel()
                await asyncio.sleep(0)
            assert not shutdown.done() and deleted == []
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await shutdown
            with pytest.raises(asyncio.CancelledError):
                await call
            assert closed == deleted == [True]
        finally:
            release.set()
            await client.aclose()
            await asyncio.gather(
                *(t for t in (call, shutdown) if t is not None), return_exceptions=True
            )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "params",
    [
        {"level": "wrong", "data": "PRIVATE"},
        {"level": "info"},
        {"level": "info", "data": "PRIVATE", "logger": []},
        {"level": "info", "data": "PRIVATE", "_meta": []},
    ],
)
def test_malformed_known_logging_notification_has_no_typed_handler_effect(params, caplog):
    async def scenario():
        server = Server(
            "post_sse",
            lambda identity: [
                {"jsonrpc": "2.0", "method": "notifications/message", "params": params},
                {"jsonrpc": "2.0", "id": identity, "result": {"content": []}},
            ],
        )
        client = server.client()
        try:
            assert await client.call_tool("read", {}) == {"content": []}
        finally:
            await client.aclose()

    caplog.set_level(10, logger="corki.mcp.inbound")
    asyncio.run(scenario())
    assert not [r for r in caplog.records if r.name == "corki.mcp.inbound"]
