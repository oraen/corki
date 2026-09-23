"""Native executor RPC packets carry real MCP Runtime discovery and calls."""

import asyncio
import base64
import json
from contextlib import asynccontextmanager

import httpx
import pytest
from test_mcp_bound_http import runtime_for
from websockets.asyncio.server import serve

from corki.mcp.executor_http import ExecutorHttpTransport
from corki.mcp.runtime_environment import MCPHTTPEnvironment, MCPRuntimeContext
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ToolResultItem
from corki.protocol.wire_numbers import WireNumber, dumps_wire


@asynccontextmanager
async def executor(http_handler, *, environment_info=None):
    packets = []

    async def peer(socket):
        async for raw in socket:
            packet = json.loads(raw)
            assert "jsonrpc" not in packet
            packets.append(packet)
            if packet["method"] == "initialize":
                assert packet["params"]["clientName"] == "corki-environment"
                result = {"sessionId": "exec-session"}
                if environment_info is not None:
                    result["environmentInfo"] = environment_info
                await socket.send(dumps_wire({"id": packet["id"], "result": result}))
            elif packet["method"] == "initialized":
                assert packet.get("params") == {}
            elif packet["method"] == "environment/info":
                await socket.send(
                    json.dumps(
                        {
                            "id": packet["id"],
                            "result": {
                                "shell": {"name": "sh", "path": "/bin/sh"},
                                "capabilities": {},
                            },
                        }
                    )
                )
            else:
                assert packet["method"] == "http/request"
                await http_handler(socket, packet)

    async with serve(peer, "127.0.0.1", 0) as server:
        url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        transport = await ExecutorHttpTransport.connect(url)
        try:
            yield transport, packets
        finally:
            await transport.aclose()


async def envelope(socket, packet, status=200, headers=(), body=b""):
    await socket.send(
        json.dumps(
            {
                "id": packet["id"],
                "result": {
                    "status": status,
                    "headers": list(headers),
                    "bodyBase64": base64.b64encode(body).decode(),
                },
            }
        )
    )


async def delta(socket, packet, seq, body=b"", *, done=False, error=None):
    await socket.send(
        json.dumps(
            {
                "method": "http/request/bodyDelta",
                "params": {
                    "requestId": packet["params"]["requestId"],
                    "seq": seq,
                    "deltaBase64": base64.b64encode(body).decode(),
                    "done": done,
                    "error": error,
                },
            }
        )
    )


@pytest.mark.parametrize("mode", ["direct", "native", "compatible", "code_mode"])
@pytest.mark.parametrize("outcome", ["success", "disconnect", "rpc_error", "sequence", "expired"])
def test_executor_http_search_call_observe_and_cleanup(tmp_path, mode, outcome):
    async def scenario():
        calls = []

        async def handle(socket, packet):
            params = packet["params"]
            assert params["url"] == "https://fixture.invalid"
            assert params["redirectPolicy"] == "stop"
            if params["method"] in ("GET", "DELETE"):
                await envelope(socket, packet, 405 if params["method"] == "GET" else 204)
                if params["streamResponse"]:
                    await delta(socket, packet, 1, done=True)
                return
            message = json.loads(base64.b64decode(params["bodyBase64"]))
            if message["method"] in ("initialize", "notifications/initialized"):
                assert type(params["timeoutMs"]) is int and params["timeoutMs"] > 0
            else:
                assert params["timeoutMs"] is None
            if "id" not in message:
                await envelope(socket, packet, 202)
                await delta(socket, packet, 1, done=True)
                return
            if message["method"] == "initialize":
                result = {
                    "capabilities": {},
                    "protocolVersion": "2025-06-18",
                    "serverInfo": {"name": "executor-mcp", "version": "1"},
                }
            elif message["method"] == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": "write",
                            "description": "needle",
                            "inputSchema": {"type": "object"},
                        }
                    ]
                }
            else:
                assert message["method"] == "tools/call"
                calls.append(message)
                if outcome == "disconnect":
                    await socket.close()
                    return
                if outcome == "rpc_error":
                    await socket.send(
                        json.dumps(
                            {
                                "id": packet["id"],
                                "error": {
                                    "code": -32000,
                                    "message": "executor HTTP failed after admission",
                                },
                            }
                        )
                    )
                    return
                if outcome == "expired" and len(calls) == 1:
                    await envelope(socket, packet, 404)
                    await delta(socket, packet, 1, done=True)
                    return
                result = {"content": [{"type": "text", "text": "executor effect"}]}
            body = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}).encode()
            # Native peers may send deltas before the response envelope.
            await delta(
                socket,
                packet,
                2 if outcome == "sequence" and message["method"] == "tools/call" else 1,
                body[:5],
            )
            await envelope(
                socket,
                packet,
                headers=[
                    {"name": "content-type", "value": "application/json"},
                    {"name": "mcp-session-id", "value": "mcp-session"},
                ],
            )
            await delta(socket, packet, 2, body[5:], done=True)

        async with executor(handle) as (transport, packets):
            runtime = await runtime_for(
                tmp_path, MCPRuntimeContext((MCPHTTPEnvironment("remote", transport),)), mode=mode
            )
            try:
                events = [event async for event in runtime.stream("needle")]
                assert isinstance(events[-1], TurnCompleted)
                assert runtime._mcp_manager.warnings == ()
                results = [
                    item
                    for item in runtime._model.requests[-1].items
                    if isinstance(item, ToolResultItem)
                ]
                assert ("executor effect" in repr(results)) == (outcome in ("success", "expired"))
                assert len(calls) == (2 if outcome == "expired" else 1)
            finally:
                await runtime.aclose()
            requests = [
                packet["params"] for packet in packets if packet["method"] == "http/request"
            ]
            assert any(packet["method"] == "DELETE" for packet in requests) == (
                outcome != "disconnect"
            )
            assert len({packet["requestId"] for packet in requests}) == len(requests)
            assert transport.is_closed == (outcome == "disconnect")
        assert transport.is_closed

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["disconnect", "sequence", "terminal", "overflow"])
def test_executor_stream_fault_never_becomes_successful_eof(fault):
    async def scenario():
        async def handle(socket, packet):
            if fault == "overflow":
                for seq in range(1, 258):
                    await delta(socket, packet, seq, b"x")
            await envelope(socket, packet)
            if fault == "disconnect":
                await socket.close()
            elif fault == "sequence":
                await delta(socket, packet, 2, b"x", done=True)
            elif fault == "terminal":
                await delta(socket, packet, 1, error="executor body failed", done=True)

        async with executor(handle) as (transport, _):
            response = await asyncio.wait_for(
                transport.handle_async_request(httpx.Request("GET", "https://fixture.invalid")), 2
            )
            try:
                with pytest.raises((httpx.TransportError, RuntimeError)):
                    await asyncio.wait_for(response.aread(), 2)
            finally:
                await response.aclose()

    asyncio.run(scenario())


def test_dropped_stream_late_frames_do_not_contaminate_next_request():
    async def scenario():
        first = None

        async def handle(socket, packet):
            nonlocal first
            await envelope(socket, packet)
            if first is None:
                first = packet
            else:
                await delta(socket, first, 1, b"stale", done=True)
                await delta(socket, packet, 1, b"fresh", done=True)

        async with executor(handle) as (transport, packets):
            first_response = await transport.handle_async_request(
                httpx.Request("GET", "https://fixture.invalid/first")
            )
            await first_response.aclose()
            second = await transport.handle_async_request(
                httpx.Request("GET", "https://fixture.invalid/second")
            )
            try:
                assert await second.aread() == b"fresh"
            finally:
                await second.aclose()
            requests = [p for p in packets if p["method"] == "http/request"]
            assert requests[0]["params"]["requestId"] != requests[1]["params"]["requestId"]

    asyncio.run(scenario())


def test_cancel_before_http_headers_does_not_close_shared_rpc_or_reuse_identity():
    async def scenario():
        seen = asyncio.Event()
        first = None

        async def handle(socket, packet):
            nonlocal first
            if first is None:
                first = packet
                seen.set()
            else:
                await envelope(socket, first)
                await delta(socket, first, 1, b"stale", done=True)
                await envelope(socket, packet)
                await delta(socket, packet, 1, b"fresh", done=True)

        async with executor(handle) as (transport, _):
            pending = asyncio.create_task(
                transport.handle_async_request(
                    httpx.Request("GET", "https://fixture.invalid/first")
                )
            )
            await asyncio.wait_for(seen.wait(), 2)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert not transport._bodies.routes and not transport._rpc._pending
            assert not transport.is_closed
            response = await transport.handle_async_request(
                httpx.Request("GET", "https://fixture.invalid/second")
            )
            try:
                assert await response.aread() == b"fresh"
            finally:
                await response.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", ["duplicate", "bad_id", "bad_error", "oversized_delta"])
def test_malformed_executor_packet_wakes_pending_headers_and_closes_connection(invalid):
    async def scenario():
        async def handle(socket, packet):
            if invalid == "duplicate":
                await socket.send('{"id":1,"id":2,"result":{}}')
            elif invalid == "bad_id":
                await socket.send('{"id":true,"result":{}}')
            elif invalid == "bad_error":
                await socket.send(
                    json.dumps({"id": packet["id"], "error": {"code": 1 << 63, "message": "bad"}})
                )
            else:
                await delta(socket, packet, 1, b"x" * (1024 * 1024 + 1))

        async with executor(handle) as (transport, _):
            with pytest.raises(httpx.ReadError):
                await asyncio.wait_for(
                    transport.handle_async_request(httpx.Request("GET", "https://fixture.invalid")),
                    3,
                )
            assert transport.is_closed
            assert not transport._bodies.routes and not transport._rpc._pending

    asyncio.run(scenario())


def test_transport_preserves_duplicate_headers_and_explicit_millisecond_timeout():
    async def scenario():
        async def handle(socket, packet):
            params = packet["params"]
            headers = [h for h in params["headers"] if h["name"].lower() == "x-test"]
            assert headers == [
                {"name": "x-test", "value": "你好"},
                {"name": "x-test", "value": "second"},
            ]
            assert params["timeoutMs"] == 1234
            await envelope(socket, packet, headers=headers)
            await delta(socket, packet, 1, b"ok", done=True)

        async with executor(handle) as (transport, _):
            request = httpx.Request(
                "GET",
                "https://fixture.invalid",
                headers=[(b"x-test", "你好".encode()), (b"x-test", b"second")],
                extensions={"corki_executor_timeout_ms": 1234},
            )
            response = await transport.handle_async_request(request)
            try:
                assert response.headers.get_list("x-test") == ["你好", "second"]
                assert await response.aread() == b"ok"
            finally:
                await response.aclose()

    asyncio.run(scenario())


def test_arbitrary_precision_executor_metadata_survives_real_handshake():
    async def scenario():
        async def handle(socket, packet):
            await envelope(socket, packet)
            await delta(socket, packet, 1, b"ok", done=True)

        info = {
            "shell": {"name": "sh", "path": "/bin/sh"},
            "capabilities": {},
            "future": WireNumber("1e999"),
        }
        async with executor(handle, environment_info=info) as (transport, _):
            assert transport._rpc.environment_info == info
            response = await transport.handle_async_request(
                httpx.Request("GET", "https://fixture.invalid")
            )
            try:
                assert await response.aread() == b"ok"
            finally:
                await response.aclose()

    asyncio.run(scenario())
