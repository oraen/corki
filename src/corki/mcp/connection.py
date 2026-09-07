"""Own one exact MCP client until its admitted calls release it."""

import asyncio
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass

from corki.mcp.client import MCPClient, MCPProtocolError


@dataclass(frozen=True, slots=True)
class MCPServerMetadata:
    """Host-owned policy; never populated from a remote server's metadata or hints."""

    pollutes_memory: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.pollutes_memory, bool):
            raise ValueError("pollutes_memory must be a boolean")


class MCPConnection:
    def __init__(
        self,
        client: MCPClient,
        on_closed: Callable,
        *,
        metadata: MCPServerMetadata | None = None,
    ) -> None:
        self.client = client
        self.metadata = metadata or MCPServerMetadata()
        self.remote_tools: frozenset[str] = frozenset()
        self._on_closed = on_closed
        self._users: dict[asyncio.Task, int] = {}
        self._retired = False
        self._close_task: asyncio.Task | None = None

    @property
    def closed(self) -> bool:
        return self._close_task is not None and self._close_task.done()

    @contextmanager
    def lease(self):
        if self._retired:
            raise MCPProtocolError("MCP connection was superseded; discover the current tool")
        task = asyncio.current_task()
        assert task is not None
        self._users[task] = self._users.get(task, 0) + 1
        try:
            yield self.client
        finally:
            remaining = self._users[task] - 1
            if remaining:
                self._users[task] = remaining
            else:
                del self._users[task]
            if self._retired and not self._users:
                self._start_close()

    async def invoke(self, method: str, *args):
        with self.lease() as client:
            return await getattr(client, method)(*args)

    async def call_tool(
        self, name, arguments, *, on_external_context=None, on_output_token_limit=None
    ):
        # Keep this exact connection alive across preparation as well as execution.
        with self.lease() as client:
            if on_output_token_limit is not None:
                settings = getattr(client, "settings", None)
                on_output_token_limit(
                    dict(getattr(settings, "tool_output_token_limits", ())).get(name)
                )
            if self.metadata.pollutes_memory and on_external_context is not None:
                await on_external_context()
            return await client.call_tool(name, arguments)

    def retire(self) -> None:
        self._retired = True
        if not self._users:
            self._start_close()

    def _start_close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(
                self.client.aclose(), name=f"mcp-{self.client.settings.name}-close"
            )
            self._close_task.add_done_callback(self._closed)

    def _closed(self, task: asyncio.Task) -> None:
        try:
            error = task.exception()
        except BaseException as exc:
            error = exc
        self._on_closed(self, error)

    async def aclose(self) -> None:
        """Shutdown differs from retirement: cancel and join any remaining users."""
        self.retire()
        users = tuple(task for task in self._users if task is not asyncio.current_task())
        for task in users:
            task.cancel()
        if users:
            await asyncio.gather(*users, return_exceptions=True)
        self._start_close()
        assert self._close_task is not None
        await asyncio.shield(self._close_task)
