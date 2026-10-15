"""Loopback callback validation must not consume another login's authorization."""

import asyncio
from contextlib import suppress
from urllib.parse import urlencode, urlsplit

import pytest

from corki.config import MCPServerSettings
from corki.mcp.oauth_callback import callback_listener


@pytest.mark.parametrize("invalid", ["state", "unicode", "issuer", "missing", "duplicate", "path"])
def test_invalid_callback_does_not_consume_code(invalid):
    async def scenario():
        settings = MCPServerSettings("fixture", "http", url="https://fixture.invalid/mcp")
        metadata = {
            "issuer": "https://issuer.invalid",
            "authorization_response_iss_parameter_supported": True,
        }
        async with callback_listener(settings, metadata, "expected") as (redirect, result):
            parsed = urlsplit(redirect)

            async def send(query, path=parsed.path):
                reader, writer = await asyncio.open_connection(parsed.hostname, parsed.port)
                try:
                    target = path + "?" + urlencode(query)
                    writer.write(f"GET {target} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode())
                    await writer.drain()
                    return await reader.read()
                finally:
                    writer.close()
                    await writer.wait_closed()

            valid = [("state", "expected"), ("iss", metadata["issuer"]), ("code", "valid-code")]
            bad = dict(valid)
            path = parsed.path
            if invalid == "state":
                bad["state"] = "other-login"
            elif invalid == "unicode":
                bad["state"] = "错误"
            elif invalid == "issuer":
                bad["iss"] = "https://other.invalid"
            elif invalid == "missing":
                del bad["iss"]
            elif invalid == "duplicate":
                bad = [*valid, ("state", "other-login")]
            else:
                path += "/other"
            assert (await send(bad, path)).startswith(b"HTTP/1.1 400")
            assert not result.done()
            assert (await send(valid)).startswith(b"HTTP/1.1 200")
            assert await result == "valid-code"
        with pytest.raises(OSError):
            await asyncio.open_connection(parsed.hostname, parsed.port)

    asyncio.run(asyncio.wait_for(scenario(), 5))


def test_repeated_cancellation_joins_listener_and_open_connections(monkeypatch):
    async def scenario():
        entered, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        captured = {}
        original = asyncio.start_server

        async def start(*args, **kwargs):
            server = await original(*args, **kwargs)
            wait_closed = server.wait_closed

            async def slow_close():
                closing.set()
                await release.wait()
                await wait_closed()

            monkeypatch.setattr(server, "wait_closed", slow_close)
            return server

        monkeypatch.setattr(asyncio, "start_server", start)

        async def login():
            settings = MCPServerSettings("fixture", "http", url="https://fixture.invalid/mcp")
            async with callback_listener(settings, {}, "state") as (redirect, result):
                captured.update(redirect=redirect, result=result)
                entered.set()
                await asyncio.Event().wait()

        owner = asyncio.create_task(login())
        await entered.wait()
        parsed = urlsplit(captured["redirect"])
        reader, writer = await asyncio.open_connection(parsed.hostname, parsed.port)
        try:
            writer.write(b"GET /unfinished")
            await writer.drain()
            await asyncio.sleep(0)
            owner.cancel()
            await closing.wait()
            owner.cancel()
            await asyncio.sleep(0)
            assert not owner.done(), "owner abandoned callback cleanup on repeated cancellation"
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await owner
            assert captured["result"].cancelled()
            # Closing a socket with an unfinished inbound request may report
            # EOF or TCP reset; both prove the owned connection was terminated.
            with suppress(ConnectionResetError):
                assert await reader.read() == b""
            with pytest.raises(OSError):
                await asyncio.open_connection(parsed.hostname, parsed.port)
        finally:
            release.set()
            writer.close()
            with suppress(ConnectionResetError):
                await writer.wait_closed()
            if not owner.done():
                owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)

    asyncio.run(asyncio.wait_for(scenario(), 5))
