"""Cancellation at TCP ownership transfer must not leave an accepted socket open."""

import asyncio

import httpx
import pytest
from anyio.abc import SocketAttribute
from anyio.lowlevel import get_async_backend

from corki.config import MCPServerSettings
from corki.mcp.client import HttpMCPClient


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
@pytest.mark.parametrize("route", ["direct", "http_proxy", "https_proxy"])
def test_cancel_after_tcp_connect_closes_socket_before_mcp_close_returns(
    monkeypatch, method, route
):
    async def scenario():
        streams, writers, tasks = [], [], set()
        peer_closed = asyncio.Event()

        async def peer(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            writers.append(writer)
            try:
                await reader.read()
                peer_closed.set()
            finally:
                writer.close()
                await writer.wait_closed()
                tasks.discard(task)

        server = await asyncio.start_server(peer, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        backend = get_async_backend()
        original_connect = backend.connect_tcp
        operation = None

        async def connect(*args, **kwargs):
            stream = await original_connect(*args, **kwargs)
            streams.append(stream)
            # No extra await or fake transport failure: cancel the MCP operation
            # exactly when a real connected stream is handed to the connector.
            operation.cancel()
            return stream

        monkeypatch.setattr(backend, "connect_tcp", connect)
        url = f"http://127.0.0.1:{port}"
        if route != "direct":
            monkeypatch.setenv("NO_PROXY", "")
            monkeypatch.delenv("no_proxy", raising=False)
            monkeypatch.setenv("HTTP_PROXY", url)
            monkeypatch.setenv("HTTPS_PROXY", url)
            url = ("https" if route == "https_proxy" else "http") + "://fixture.invalid/"
        client = HttpMCPClient(MCPServerSettings("handoff", "http", url=url))
        try:
            operation = asyncio.create_task(client._http_request(method, httpx.Headers()))
            with pytest.raises(asyncio.CancelledError):
                await operation
            await client.aclose()
            assert streams and all(
                s.extra(SocketAttribute.raw_socket).fileno() == -1 for s in streams
            )
            await asyncio.wait_for(peer_closed.wait(), 1)
        finally:
            await client.aclose()
            for stream in streams:
                await stream.aclose()
            for writer in writers:
                writer.close()
                await writer.wait_closed()
            server.close()
            await server.wait_closed()
            await asyncio.gather(*tasks)

    asyncio.run(scenario())


def test_cancel_during_tls_handshake_reclaims_connected_tcp_socket(monkeypatch):
    async def scenario():
        streams, writers, tasks = [], [], set()
        handshake, peer_closed = asyncio.Event(), asyncio.Event()

        async def peer(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            writers.append(writer)
            try:
                assert await reader.read(4096)  # A real ClientHello, not a fake TLS stream.
                handshake.set()
                await reader.read()
                peer_closed.set()
            finally:
                writer.close()
                await writer.wait_closed()
                tasks.discard(task)

        backend = get_async_backend()
        original = backend.connect_tcp

        async def connect(*args, **kwargs):
            stream = await original(*args, **kwargs)
            streams.append(stream)
            return stream

        monkeypatch.setattr(backend, "connect_tcp", connect)
        server = await asyncio.start_server(peer, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        client = HttpMCPClient(MCPServerSettings("tls", "http", url=f"https://127.0.0.1:{port}"))
        operation = asyncio.create_task(client._http_request("GET", httpx.Headers()))
        try:
            await asyncio.wait_for(handshake.wait(), 1)
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation
            await client.aclose()
            assert streams and all(
                s.extra(SocketAttribute.raw_socket).fileno() == -1 for s in streams
            )
            await asyncio.wait_for(peer_closed.wait(), 1)
        finally:
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            await client.aclose()
            for stream in streams:
                await stream.aclose()
            for writer in writers:
                writer.close()
                await writer.wait_closed()
            server.close()
            await server.wait_closed()
            await asyncio.gather(*tasks)

    asyncio.run(scenario())
