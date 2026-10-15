"""Cancelled readers/writers cannot strand exact-catalog authority."""

import asyncio

import pytest

from corki.mcp.catalog_revision import MCPCatalogRevision


def test_cancelled_waiting_writer_releases_queued_readers():
    async def scenario():
        revision = MCPCatalogRevision()

        async def writer():
            async with revision.write():
                raise AssertionError("writer passed active reader")

        async def reader():
            async with revision.read() as value:
                return value

        async with revision.read():
            waiting_writer = asyncio.create_task(writer())
            await asyncio.sleep(0)
            waiting_reader = asyncio.create_task(reader())
            await asyncio.sleep(0)
            assert not waiting_reader.done()
            waiting_writer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting_writer
            assert await asyncio.wait_for(waiting_reader, 1) == 0
        async with revision.write():
            revision.value += 1
        assert await reader() == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("writer", [False, True])
def test_cancellation_releases_waiting_reader_or_admitted_writer(writer):
    async def scenario():
        revision = MCPCatalogRevision()
        entered = asyncio.Event()

        async def work():
            async with revision.write() if writer else revision.read():
                entered.set()
                await asyncio.Event().wait()

        if writer:
            task = asyncio.create_task(work())
            await entered.wait()
        else:
            async with revision.write():
                task = asyncio.create_task(work())
                await asyncio.sleep(0)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert not entered.is_set()
        if writer:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        async with asyncio.timeout(1):
            async with revision.read() as value:
                assert value == 0
            async with revision.write():
                revision.value += 1

    asyncio.run(scenario())
