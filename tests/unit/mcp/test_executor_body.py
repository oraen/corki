"""Bounded body ownership under early frames, drops, cancellation and shared pressure."""

import asyncio
import gc
import weakref

import pytest

from corki.mcp.executor_body import BodyRouter
from corki.mcp.executor_wire import BodyDelta, ExecutorProtocolError


def test_shared_byte_limit_releases_on_consume_and_drop(monkeypatch):
    monkeypatch.setattr("corki.mcp.executor_body.MAX_QUEUED_BYTES", 10)

    async def scenario():
        router = BodyRouter()
        first, second = router.register("a"), router.register("b")
        first.deliver(BodyDelta("a", 1, b"123456", False, None))
        second.deliver(BodyDelta("b", 1, b"12345", False, None))
        with pytest.raises(ExecutorProtocolError, match="byte limit"):
            await anext(second.__aiter__())
        assert router.queued_bytes == 6 and "a" in router.routes and "b" not in router.routes
        stream = first.__aiter__()
        assert await anext(stream) == b"123456"
        assert router.queued_bytes == 0
        third = router.register("c")
        third.deliver(BodyDelta("c", 1, b"abcdefghij", False, None))
        ref = weakref.ref(third)
        del third
        gc.collect()
        assert ref() is None and router.queued_bytes == 0
        await stream.aclose()
        assert not router.routes

    asyncio.run(scenario())


def test_full_queue_disconnect_reports_error_after_queued_data():
    async def scenario():
        router = BodyRouter()
        body = router.register("a")
        for seq in range(1, 257):
            body.deliver(BodyDelta("a", seq, b"x", False, None))
        router.fail_all("disconnected")
        count = 0
        with pytest.raises(ExecutorProtocolError, match="disconnected"):
            async for chunk in body:
                assert chunk == b"x"
                count += 1
        assert count == 256 and router.queued_bytes == 0 and not router.routes

    asyncio.run(scenario())


def test_body_consumer_cancel_releases_route_and_bytes():
    async def scenario():
        router = BodyRouter()
        body = router.register("a")

        async def consume():
            async for _ in body:
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not router.routes and router.queued_bytes == 0

    asyncio.run(scenario())


def test_terminal_data_is_returned_once_and_error_wins_over_data():
    async def scenario():
        router = BodyRouter()
        body = router.register("a")
        body.deliver(BodyDelta("a", 1, b"tail", True, None))
        assert [chunk async for chunk in body] == [b"tail"]
        failed = router.register("b")
        failed.deliver(BodyDelta("b", 1, b"not success", True, ""))
        with pytest.raises(ExecutorProtocolError, match="failed"):
            await anext(failed.__aiter__())
        assert router.queued_bytes == 0 and not router.routes

    asyncio.run(scenario())
