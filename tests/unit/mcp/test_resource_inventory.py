"""Aggregate limits and cancellation are owned across all resource pages."""

import asyncio
from types import SimpleNamespace

import pytest

from corki.mcp.json_rpc import MCPProtocolError
from corki.mcp.resource_inventory import aggregate_resources, collect_resource_pages


@pytest.mark.parametrize("count", [1024, 1025, 2048, 2049])
def test_aggregate_resource_item_limit(count):
    class Client:
        settings = SimpleNamespace(timeout_seconds=1)

        async def list_resources(self, cursor):
            return {"resources": [{"name": "x", "uri": "fixture:x"}] * count}

    async def scenario():
        if count > 2048:
            with pytest.raises(MCPProtocolError, match="2048"):
                await collect_resource_pages(Client(), "resources")
        else:
            assert len(await collect_resource_pages(Client(), "resources")) == count

    asyncio.run(scenario())


@pytest.mark.parametrize("variant", ["empty", "repeated", "oversized", "pages"])
def test_aggregate_resource_cursor_and_page_boundaries(variant):
    requests = []

    class Client:
        settings = SimpleNamespace(timeout_seconds=1)

        async def list_resource_templates(self, cursor):
            requests.append(cursor)
            next_cursor = {
                "empty": "" if len(requests) == 1 else None,
                "repeated": "same",
                "oversized": "x" * 65537,
                "pages": str(len(requests)),
            }[variant]
            return {"resourceTemplates": [], "nextCursor": next_cursor}

    async def scenario():
        if variant == "empty":
            assert await collect_resource_pages(Client(), "templates") == []
            assert requests == [None, ""]
        else:
            with pytest.raises(MCPProtocolError):
                await collect_resource_pages(Client(), "templates")
            assert len(requests) == {"repeated": 2, "oversized": 1, "pages": 100}[variant]

    asyncio.run(scenario())


def test_repeated_cancel_does_not_interrupt_resource_collectors_cleanup():
    async def scenario():
        started, cleaning, release = [asyncio.Event() for _ in range(3)]
        finished = []

        class Client:
            settings = SimpleNamespace(timeout_seconds=10)

            async def list_resources(self, cursor):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaning.set()
                    await release.wait()
                    finished.append("closed")

        operation = asyncio.create_task(aggregate_resources((("docs", Client()),), "resources"))
        try:
            await asyncio.wait_for(started.wait(), 1)
            operation.cancel()
            await asyncio.wait_for(cleaning.wait(), 1)
            operation.cancel()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert not operation.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await operation
            assert finished == ["closed"]
        finally:
            release.set()
            await asyncio.gather(operation, return_exceptions=True)

    asyncio.run(scenario())


def test_resource_collection_uses_one_deadline_across_pages():
    async def scenario():
        entered = []

        class Client:
            settings = SimpleNamespace(timeout_seconds=0.05)

            async def list_resources(self, cursor):
                entered.append(cursor)
                # Each page is faster than the timeout, but three exceed it.
                await asyncio.sleep(0.03)
                return {"resources": [], "nextCursor": str(len(entered))}

        with pytest.raises(TimeoutError):
            await collect_resource_pages(Client(), "resources")
        assert 1 <= len(entered) <= 2

    asyncio.run(scenario())
