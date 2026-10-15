"""Connector fallback, timeout and host-owned carrier boundaries."""

import asyncio
import socket

import anyio
import httpcore
import httpx
import pytest
from anyio.lowlevel import get_async_backend
from httpcore._backends.anyio import AnyIOStream

from corki.config import MCPServerSettings
from corki.http_connect import OwnedConnectBackend, OwnedNetworkStream
from corki.mcp.client import HttpMCPClient


def test_dns_ipv6_failure_falls_back_to_ipv4_and_preserves_stream_io(monkeypatch):
    async def scenario():
        tasks = set()

        async def echo(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            try:
                writer.write(await reader.readexactly(5))
                await writer.drain()
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()
                tasks.discard(task)

        server = await asyncio.start_server(echo, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        async def addresses(host, requested_port, **kwargs):
            assert host == "fixture.invalid" and requested_port == port
            return [
                (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", port, 0, 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port)),
            ]

        monkeypatch.setattr(anyio, "getaddrinfo", addresses)
        backend = get_async_backend()
        original, attempted = backend.connect_tcp, []

        async def connect(host, *args):
            attempted.append(host)
            if host == "::1":
                raise OSError("fixture IPv6 connection refused")
            return await original(host, *args)

        monkeypatch.setattr(backend, "connect_tcp", connect)
        stream = None
        try:
            stream = await OwnedConnectBackend().connect_tcp("fixture.invalid", port, timeout=1)
            await stream.write(b"hello", timeout=1)
            assert await stream.read(5, timeout=1) == b"hello"
            assert attempted == ["::1", "127.0.0.1"]
        finally:
            if stream is not None:
                await stream.aclose()
            server.close()
            await server.wait_closed()
            await asyncio.gather(*tasks)

    asyncio.run(scenario())


def test_connect_timeout_cancels_and_joins_address_attempt(monkeypatch):
    async def scenario():
        exited = asyncio.Event()

        async def connect(*args):
            try:
                await asyncio.Event().wait()
            finally:
                exited.set()

        monkeypatch.setattr(get_async_backend(), "connect_tcp", connect)
        with pytest.raises(httpcore.ConnectTimeout):
            await OwnedConnectBackend().connect_tcp("127.0.0.1", 1, timeout=0.01)
        assert exited.is_set()

    asyncio.run(scenario())


def test_explicit_host_http_transport_is_not_mutated():
    async def scenario():
        transport = httpx.AsyncHTTPTransport()
        original = transport._pool._network_backend
        client = HttpMCPClient(
            MCPServerSettings("host", "http", url="https://fixture.invalid"), transport=transport
        )
        try:
            assert transport._pool._network_backend is original
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_successful_tls_upgrade_retains_nested_cancellation_owner(monkeypatch):
    async def scenario():
        original, upgraded = object(), object()

        async def tls(self, *args):
            assert self._stream is original
            return AnyIOStream(upgraded)

        monkeypatch.setattr(AnyIOStream, "start_tls", tls)
        result = await OwnedNetworkStream(original).start_tls(None)
        assert isinstance(result, OwnedNetworkStream) and result._stream is upgraded

    asyncio.run(scenario())
