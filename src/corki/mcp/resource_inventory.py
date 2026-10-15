"""Whole-catalog resource collection, separate from named single-page RPCs."""

import asyncio
import logging

from corki.mcp.json_rpc import MCPProtocolError
from corki.mcp.request_policy import effective_timeout

logger = logging.getLogger(__name__)


async def collect_resource_pages(client, kind: str) -> list[dict]:
    """Bound one server's entire collection, including time across all its pages."""
    field, method = (
        ("resources", client.list_resources)
        if kind == "resources"
        else ("resourceTemplates", client.list_resource_templates)
    )
    items, seen = [], set()
    cursor = None
    async with asyncio.timeout(effective_timeout(client, client.settings.timeout_seconds)):
        for _ in range(100):
            page = await method(cursor)
            if len(page[field]) > 2048 - len(items):
                raise MCPProtocolError(f"{field} exceeded the catalog limit of 2048 items")
            items.extend(page[field])
            cursor = page.get("nextCursor")
            if cursor is None:
                return items
            if len(cursor.encode("utf-8")) > 65536:
                raise MCPProtocolError(f"{field} returned an oversized pagination cursor")
            if cursor in seen:
                raise MCPProtocolError(f"{field} returned a repeated pagination cursor")
            seen.add(cursor)
    raise MCPProtocolError(f"{field} exceeded the pagination limit of 100 pages")


async def aggregate_resources(captured: tuple, kind: str) -> dict:
    """Keep healthy servers and join every collector before releasing caller leases."""

    async def collect(name, client):
        try:
            return name, await collect_resource_pages(client, kind)
        except Exception as error:
            logger.warning("Failed to list MCP %s for server %r: %s", kind, name, error)
            return name, []

    tasks = [asyncio.create_task(collect(name, client)) for name, client in captured]
    joined = asyncio.gather(*tasks, return_exceptions=True)
    try:
        # Caller cancellation must not repeatedly cancel a child's async cleanup.
        results = await asyncio.shield(joined)
    finally:
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        interrupted = False
        while not joined.done():
            try:
                await asyncio.shield(joined)
            except asyncio.CancelledError:
                interrupted = True
        joined.result()
        if interrupted:
            raise asyncio.CancelledError
    field = "resources" if kind == "resources" else "resourceTemplates"
    completed = []
    for (name, _), result in zip(captured, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning("Failed MCP %s collector for server %r: %s", kind, name, result)
        else:
            completed.append(result)
    return {
        field: [{"server": name, **item} for name, items in sorted(completed) for item in items]
    }
