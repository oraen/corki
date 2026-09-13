"""Own one exact MCP client until its admitted calls release it."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass

from corki.config import MCPServerSettings
from corki.mcp.approvals import MCPToolAnnotations
from corki.mcp.catalog_policy import REGULAR_CATALOG_LIMIT
from corki.mcp.client import MCPClient, MCPProtocolError
from corki.mcp.projection import MCPToolProjection
from corki.mcp.reconciliation import MCPTransportOwner
from corki.mcp.request_policy import request_timeout
from corki.mcp.runtime_environment import MCPHTTPEnvironment
from corki.mcp.tool_catalog_cache import CatalogCacheContext, CatalogSnapshot
from corki.mcp.tool_filter import MCPToolFilter


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
        tool_filter: MCPToolFilter | None = None,
        settings: MCPServerSettings | None = None,
        environment: MCPHTTPEnvironment | None = None,
        agent_plugin: bool = False,
        cache_context: CatalogCacheContext | None = None,
        catalog_item_limit: int = REGULAR_CATALOG_LIMIT,
        _owner: MCPTransportOwner | None = None,
    ) -> None:
        self.client = client
        self.settings = settings if settings is not None else getattr(client, "settings", None)
        self._owner = _owner or MCPTransportOwner(
            client,
            self.settings,
            environment,
            agent_plugin,
            cache_context,
            catalog_item_limit=catalog_item_limit,
        )
        self._owner.retain()
        self.metadata = metadata or MCPServerMetadata()
        self.tool_filter = tool_filter or MCPToolFilter()
        self.remote_tools: frozenset[str] = frozenset()
        self.approval_annotations: dict[str, MCPToolAnnotations] = {}
        self.call_tools: dict[str, MCPToolProjection] = {}
        self._on_closed = on_closed
        self._users: dict[asyncio.Task, int] = {}
        self._retired = False
        self._close_task: asyncio.Task | None = None
        # Lifetime is held by generation/handoff leases, not by this physical view.
        self.catalog_override: CatalogSnapshot | None = None

    async def start(self) -> None:
        """Initialize a new transport and retain its unfiltered catalog."""
        await self._owner.start()

    @property
    def startup_pending(self) -> bool:
        return not self._retired and self._owner.startup_pending

    def can_reuse_pending(
        self,
        settings,
        environment=None,
        *,
        agent_plugin=False,
        catalog_item_limit=REGULAR_CATALOG_LIMIT,
    ) -> bool:
        """Keep only active, compatible physical startup across immutable views."""
        return not self._retired and self._owner.can_reuse_pending(
            settings,
            environment,
            agent_plugin=agent_plugin,
            catalog_item_limit=catalog_item_limit,
        )

    def cancel_startup(self) -> None:
        """Forward explicit startup cancellation to the shared physical owner."""
        self._owner.cancel_startup()

    @property
    def definitions(self) -> tuple:
        """Isolate the cached raw catalog from this view's filtered definitions."""
        if self.catalog_override is not None:
            return deepcopy(self.catalog_override.definitions)
        return self._owner.definitions

    @property
    def server_instructions(self) -> str | None:
        """Instructions captured with the physical session's initialized catalog."""
        if self.catalog_override is not None:
            return self.catalog_override.instructions
        return self._owner.server_instructions

    def can_reuse(
        self,
        settings: MCPServerSettings,
        environment: MCPHTTPEnvironment | None = None,
        *,
        agent_plugin: bool = False,
        catalog_item_limit: int = REGULAR_CATALOG_LIMIT,
    ) -> bool:
        """Require a healthy initialized transport with unchanged connection inputs."""
        return not self._retired and self._owner.can_reuse(
            settings,
            environment,
            agent_plugin=agent_plugin,
            catalog_item_limit=catalog_item_limit,
        )

    def fork(self, settings: MCPServerSettings, metadata: MCPServerMetadata) -> MCPConnection:
        """Own a new policy view without mutating the old view or physical client."""
        view = MCPConnection(
            self.client,
            self._on_closed,
            metadata=metadata,
            tool_filter=MCPToolFilter.from_settings(settings),
            settings=settings,
            _owner=self._owner,
        )
        return view

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
            with request_timeout(self.client, getattr(self.settings, "timeout_seconds", None)):
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
        self,
        name,
        arguments,
        *,
        on_external_context=None,
        on_output_token_limit=None,
        before_call: Callable[[], Awaitable[None]] | None = None,
    ):
        # Keep this exact connection alive across preparation as well as execution.
        with self.lease() as client:
            if on_output_token_limit is not None:
                on_output_token_limit(
                    dict(getattr(self.settings, "tool_output_token_limits", ())).get(name)
                )
            # Presentation policy also applies to approval rejection/failure.
            if before_call is not None:
                await before_call()
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
                self._owner.release(), name=f"mcp-{self.client.settings.name}-view-close"
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
