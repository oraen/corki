import asyncio

import pytest

from corki.config import MCPServerSettings
from corki.mcp.client import StdioMCPClient


@pytest.mark.parametrize("failure", [False, True])
def test_cancelled_close_joins_owned_cleanup_and_preserves_cancellation(monkeypatch, failure):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        client = StdioMCPClient(MCPServerSettings("fixture", "stdio", command="unused"))

        async def close():
            calls.append("close")
            entered.set()
            await release.wait()
            if failure:
                raise OSError("fixture close failure")

        monkeypatch.setattr(client, "_close", close)
        waiter = asyncio.create_task(client.aclose())
        await entered.wait()
        waiter.cancel()
        await asyncio.sleep(0)
        waiter.cancel()
        await asyncio.sleep(0)
        assert not waiter.done() and not client._close_task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        if failure:
            with pytest.raises(OSError, match="fixture close failure"):
                await client.aclose()
        else:
            await client.aclose()
        assert calls == ["close"] and client.is_closed

    asyncio.run(scenario())
