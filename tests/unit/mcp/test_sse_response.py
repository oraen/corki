import asyncio
import json

import httpx
import pytest

from corki.config import MCPServerSettings
from corki.mcp.client import HttpMCPClient, MCPProtocolError, _decode_http_response, _read_stdio
from corki.mcp.sse import SseEvent, SseParser


@pytest.mark.parametrize("ending", [b"\n", b"\r", b"\r\n"])
@pytest.mark.parametrize("chunk_size", [1, 17, 4096])
def test_completed_response_does_not_wait_for_eof(ending, chunk_size):
    async def scenario():
        ended = []
        packet = json.dumps(
            {"id": 1, "result": {"content": [{"type": "text", "text": "完成"}]}}, ensure_ascii=False
        ).encode()
        wire = (
            b"\xef\xbb\xbf: ping"
            + ending
            + b"event: message"
            + ending
            + b"data: "
            + packet
            + ending * 2
        )

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                for index in range(0, len(wire), chunk_size):
                    yield wire[index : index + chunk_size]
                await asyncio.Event().wait()

            async def aclose(self):
                ended.append(True)

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid", timeout_seconds=0.1),
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, stream=Stream()
                )
            ),
        )
        try:
            assert await client.call_tool("read", {}) == {
                "content": [{"type": "text", "text": "完成"}]
            }
            assert ended == [True]
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "prefix",
    [
        'event: ping\ndata: {"id":1,"result":{"wrong":true}}\n\n',
        'data: {"id":1,"method":"sampling/createMessage","params":{}}\n\n',
    ],
)
def test_control_or_server_request_cannot_complete_outbound_request(prefix):
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text=prefix + 'data: {"id":1,"result":{"right":true}}\n\n',
    )
    assert _decode_http_response(response, expected_id=1)["result"] == {"right": True}


@pytest.mark.parametrize("ending", ["", "\n", "\r\n"])
def test_unterminated_event_is_not_a_complete_response(ending):
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        text='data: {"id":1,"result":{}}' + ending,
    )
    with pytest.raises(MCPProtocolError):
        _decode_http_response(response, expected_id=1)


@pytest.mark.parametrize("identity", ["1", "+1", "0001"])
def test_numeric_string_response_id_matches_integer_request(identity):
    async def scenario():
        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"id": identity, "result": {"content": []}})
            ),
        )
        try:
            assert await client.call_tool("read", {}) == {"content": []}
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("identity", ["1", "+1", "0001"])
def test_stdio_numeric_response_identity_does_not_accept_inbound_request(identity):
    async def scenario():
        reader = asyncio.StreamReader()
        reader.feed_data((json.dumps({"id": identity, "method": "roots/list"}) + "\n").encode())
        reader.feed_data((json.dumps({"id": identity, "result": {"ok": True}}) + "\n").encode())
        reader.feed_eof()
        future = asyncio.get_running_loop().create_future()
        await _read_stdio(reader, {1: future})
        assert (await future)["result"] == {"ok": True}

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        b"bogus: value\n\n",
        b"data\n\n",
        b"event: a\nevent: b\n\n",
        b"id: 1\nid: 2\n\n",
        b"retry: 1\nretry: 2\n\n",
        b"retry: -1\n\n",
        b"retry: 18446744073709551616\n\n",
        b"data: \xff\n\n",
    ],
)
def test_parser_rejects_source_invalid_fields_without_echoing_payload(payload):
    with pytest.raises(MCPProtocolError) as caught:
        list(SseParser(1024).feed(payload))
    assert len(str(caught.value)) < 100


def test_parser_preserves_source_whitespace_fields_and_event_boundaries():
    wire = b"data:  first\r\ndata:\tsecond\rid: bad\0value\nid: keep\nretry: +7 \nevent:\n\n"
    parser = SseParser(1024)
    events = [event for byte in wire for event in parser.feed(bytes([byte]))]
    assert events == [SseEvent(data=" first\n\tsecond", event="", id="keep", retry=7)]


def test_event_limit_resets_and_comment_traffic_does_not_accumulate():
    parser = SseParser(9)
    assert list(parser.feed(b": ping\n" * 10000)) == []
    assert list(parser.feed(b"data: ab\n\n" * 100)) == [SseEvent(data="ab")] * 100
    with pytest.raises(MCPProtocolError, match="exceeds"):
        list(parser.feed(b"data: abc\n"))


def test_incomplete_line_is_bounded_before_append():
    parser = SseParser(8)
    assert list(parser.feed(b"data: ab")) == []
    with pytest.raises(MCPProtocolError, match="exceeds"):
        list(parser.feed(b"x" * 10000))


@pytest.mark.parametrize("close_fails", [False, True])
def test_completed_sse_still_owns_response_close_during_repeated_cancel(close_fails):
    async def scenario():
        closing, release, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"id":1,"result":{"content":[]}}\n\n'
                raise AssertionError("result already complete")

            async def aclose(self):
                closing.set()
                await release.wait()
                cleaned.set()
                if close_fails:
                    raise OSError("PRIVATE cleanup detail")

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, stream=Stream()
                )
            ),
        )
        task = asyncio.create_task(client.call_tool("read", {}))
        try:
            await asyncio.wait_for(closing.wait(), 2)
            for _ in range(3):
                task.cancel()
                await asyncio.sleep(0)
            assert not task.done() and not cleaned.is_set()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert cleaned.is_set()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["initialize", "tools/list", "tools/call"])
def test_sse_read_failure_is_not_a_retryable_post_send_failure(phase):
    async def scenario():
        attempts, closed = [], []

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b": ping\n"
                raise httpx.ReadError("PRIVATE body detail")

            async def aclose(self):
                closed.append(True)

        def handle(request):
            message = json.loads(request.content)
            if "id" not in message:
                return httpx.Response(202)
            if message["method"] == phase:
                attempts.append(message)
                return httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, stream=Stream()
                )
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
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(handle),
        )
        try:
            with pytest.raises(MCPProtocolError) as caught:
                await client.start()
                await client.request(phase, {})
            assert "PRIVATE" not in str(caught.value)
            assert len(attempts) == len(closed) == 1
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_loopback_completed_sse_closes_connection_without_peer_eof():
    async def scenario():
        peers, errors, seen = set(), [], []
        disconnected = asyncio.Event()

        async def serve(reader, writer):
            task = asyncio.current_task()
            peers.add(task)
            try:
                head = await reader.readuntil(b"\r\n\r\n")
                length = next(
                    int(line.split(b":", 1)[1])
                    for line in head.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                message = json.loads(await reader.readexactly(length))
                seen.append(message)
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                    b"Transfer-Encoding: chunked\r\n\r\n"
                )
                payload = (
                    "data: "
                    + json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": str(message["id"]),
                            "result": {"content": [{"type": "text", "text": "live"}]},
                        }
                    )
                    + "\r\n\r\n"
                ).encode()
                for offset in range(0, len(payload), 3):
                    part = payload[offset : offset + 3]
                    writer.write(f"{len(part):x}\r\n".encode() + part + b"\r\n")
                    await writer.drain()
                # Deliberately never send the zero chunk or close from the server side.
                assert await asyncio.wait_for(reader.read(), 2) == b""
                disconnected.set()
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
                timeout_seconds=0.5,
            )
        )
        try:
            assert await client.call_tool("read", {}) == {
                "content": [{"type": "text", "text": "live"}]
            }
            await asyncio.wait_for(disconnected.wait(), 2)
            assert len(seen) == 1
        finally:
            await client.aclose()
            server.close()
            await server.wait_closed()
            await asyncio.gather(*peers)
        assert errors == []

    asyncio.run(scenario())
