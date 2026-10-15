"""Sampling-owned resource routing, independent of the manager's latest catalog."""

import asyncio
from contextlib import ExitStack, contextmanager

from corki.mcp.model_access import ensure_model_access
from corki.mcp.resource_inventory import aggregate_resources


class MCPResourceBinding:
    """Retain one connection generation until its Step and admitted calls release it."""

    def __init__(self, generation, ready, release) -> None:
        self._generation = generation
        self._ready = dict(ready)
        self._release = release
        self._references = 1
        self._close_task = None
        self.closed = asyncio.get_running_loop().create_future()

    @contextmanager
    def lease(self):
        # Admission precedes ledger/event/gate awaits, not just the network RPC.
        if self._references == 0:
            raise RuntimeError("MCP resource Step binding is closed")
        self._references += 1
        try:
            yield self
        finally:
            self.release()

    @property
    def released(self) -> bool:
        return self._references == 0

    def release(self) -> None:
        assert self._references > 0
        self._references -= 1
        if self._references:
            return
        self._close_task = asyncio.create_task(self._close(), name="mcp-resource-binding-close")
        self._close_task.add_done_callback(self._finish)

    async def _close(self):
        try:
            results = await asyncio.gather(
                *(connection.aclose() for connection in self._ready.values()),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    raise result
        finally:
            cleanup = self._release(self._generation)
            if cleanup is not None:
                await asyncio.shield(cleanup)

    def _finish(self, task) -> None:
        try:
            task.result()
        except BaseException as exc:
            self.closed.set_exception(exc)
        else:
            self.closed.set_result(None)

    async def _client(self, server):
        if server in self._ready:
            return self._ready[server]
        generation = self._generation
        if generation is not None and server in generation.tasks:
            return await generation.wait_for_client(server)
        raise KeyError(f"unknown or unavailable MCP server: {server}")

    async def list_capability(self, kind, server, cursor=None, *, excluded_servers=frozenset()):
        if kind not in {"resources", "templates"}:
            raise ValueError(f"unknown MCP resource capability: {kind}")
        ensure_model_access(server, excluded_servers)
        if server is None:
            if cursor is not None:
                raise ValueError("cursor can only be used when a server is specified")
            with ExitStack() as leases:
                clients = tuple(
                    (name, leases.enter_context(connection.lease()))
                    for name, connection in self._ready.items()
                    if name not in excluded_servers
                )
                return await aggregate_resources(clients, kind)
        connection = await self._client(server)
        method = "list_resources" if kind == "resources" else "list_resource_templates"
        field = "resources" if kind == "resources" else "resourceTemplates"
        page = await connection.invoke(method, cursor)
        result = {"server": server, field: [{"server": server, **item} for item in page[field]]}
        if page.get("nextCursor") is not None:
            result["nextCursor"] = page["nextCursor"]
        return result

    async def read_resource(self, server, uri):
        return await (await self._client(server)).invoke("read_resource", uri)
