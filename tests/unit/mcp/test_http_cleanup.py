import asyncio

import httpx
import pytest

from corki.config import MCPServerSettings
from corki.mcp.client import HttpMCPClient


@pytest.mark.parametrize("phase", ["read", "eof", "accepted"])
@pytest.mark.parametrize("close_fails", [False, True])
@pytest.mark.parametrize("repeat", [False, True])
def test_repeated_cancel_joins_one_response_close_and_remains_control_flow(
    phase, close_fails, repeat, caplog
):
    async def scenario():
        reading, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls, close_calls, finished = [], [], []

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                reading.set()
                if phase == "read":
                    await asyncio.Future()
                yield b'{"id":1,"result":{}}'

            async def aclose(self):
                close_calls.append(True)
                closing.set()
                await release.wait()
                finished.append(True)
                if close_fails:
                    raise OSError("PRIVATE cleanup failure")

        def handle(request):
            calls.append(request)
            return httpx.Response(202 if phase == "accepted" else 200, stream=Stream())

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(handle),
        )
        task = asyncio.create_task(
            client.notify("notifications/initialized", {})
            if phase == "accepted"
            else client.request("tools/call", {})
        )
        try:
            if phase == "read":
                await asyncio.wait_for(reading.wait(), 2)
                task.cancel()
            await asyncio.wait_for(closing.wait(), 2)
            for _ in range(3 if repeat else int(phase != "read")):
                task.cancel()
                await asyncio.sleep(0)
            assert not task.done(), "caller finished before owned response cleanup"
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert len(calls) == len(close_calls) == len(finished) == 1
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await client.aclose()

    asyncio.run(scenario())
    assert "PRIVATE" not in caplog.text


@pytest.mark.parametrize("read_fails", [False, True])
def test_close_failure_does_not_report_success_or_replace_primary_read_error(read_fails):
    async def scenario():
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                if read_fails:
                    raise httpx.ReadError("primary failure")
                yield b'{"id":1,"result":{}}'

            async def aclose(self):
                raise OSError("cleanup failure")

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid"),
            transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Stream())),
        )
        try:
            with pytest.raises(httpx.ReadError if read_fails else OSError):
                await client.request("tools/call", {})
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_request_timeout_waits_for_owned_close_before_reporting_unknown_outcome():
    async def scenario():
        closing, release = asyncio.Event(), asyncio.Event()
        finished = []

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'{"id":1,"result":{}}'

            async def aclose(self):
                closing.set()
                await release.wait()
                finished.append(True)

        client = HttpMCPClient(
            MCPServerSettings("docs", "http", url="https://fixture.invalid", timeout_seconds=0.05),
            transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Stream())),
        )
        task = asyncio.create_task(client.request("tools/call", {}))
        try:
            await asyncio.wait_for(closing.wait(), 2)
            await asyncio.sleep(0.08)
            assert task.cancelling() and not task.done()
            release.set()
            with pytest.raises(TimeoutError):
                await task
            assert finished == [True]
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await client.aclose()

    asyncio.run(scenario())
