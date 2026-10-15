"""Owned preparation state retained while optional MCP connections are pending."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from corki.config import MCPServerSettings
from corki.config.layers import LocalConfigState
from corki.mcp.admission import MCPUnavailableError
from corki.mcp.catalog import MCPCatalog
from corki.mcp.catalog_revision import MCPCatalogRevision
from corki.mcp.client import MCPProtocolError
from corki.mcp.connection import MCPConnection, MCPServerMetadata
from corki.mcp.names import MCPToolIdentity
from corki.mcp.tool_catalog_cache import CatalogCacheContext, CatalogSnapshot
from corki.mcp.tools import MCPTool


@dataclass
class MCPPreparation:
    """One captured directory and its independently completing server tasks."""

    desired: tuple[MCPServerSettings, ...]
    catalog: MCPCatalog | None
    force_reconnect: bool
    owned: list[MCPConnection]
    staged: dict[str, MCPConnection]
    prepared: dict[str, tuple[MCPTool, ...]]
    warnings: list[str]
    server_warnings: dict[str, str]
    required_failures: dict[str, str]
    denied: dict[str, str]
    tasks: dict[str, asyncio.Task[bool]] = field(default_factory=dict)
    deadline: float | None = None
    published_count: int = -1
    catalog_contexts: dict[str, CatalogCacheContext] = field(default_factory=dict)
    pending_connections: dict[str, MCPConnection] = field(default_factory=dict)
    published_cached: dict[str, CatalogSnapshot] = field(default_factory=dict)
    cache_eligible: set[str] = field(default_factory=set)
    startup_triggers: dict[str, asyncio.Event] = field(default_factory=dict)
    tool_identities: dict[str, tuple[MCPToolIdentity, ...]] = field(default_factory=dict)
    binding_users: int = 0
    cleanup_task: asyncio.Task | None = None
    failed_startups: set[str] = field(default_factory=set)
    initial_failures: dict[str, str] = field(default_factory=dict)
    catalog_epoch: int = 0
    published_epoch: int = -1
    catalog_revision: MCPCatalogRevision = field(default_factory=MCPCatalogRevision)
    server_metadata: dict[str, MCPServerMetadata] = field(default_factory=dict)
    approval_configuration: LocalConfigState | None = None

    def is_dormant(self, server: str) -> bool:
        trigger = self.startup_triggers.get(server)
        return trigger is not None and not trigger.is_set()

    def activate(self, server: str) -> None:
        if trigger := self.startup_triggers.get(server):
            trigger.set()

    async def wait_for_client(self, server: str, *, waiter=None) -> MCPConnection:
        """Choose readiness now; later recovery cannot rewrite a selected startup."""
        if connection := self.staged.get(server):
            return connection
        task = self.tasks.get(server)
        if task is None:
            raise MCPUnavailableError(f"unknown MCP server: {server}")
        self.activate(server)
        try:
            if waiter is None:
                # Request cancellation never cancels the shared initial startup.
                await asyncio.shield(task)
            else:
                await waiter()
        except asyncio.CancelledError:
            caller = asyncio.current_task()
            if task.cancelled() and caller is not None and not caller.cancelling():
                raise MCPProtocolError(f"MCP server '{server}' startup was cancelled") from None
            raise
        if reason := self.denied.get(server):
            raise MCPProtocolError(f"MCP server '{server}' is disabled by {reason}")
        if task.cancelled():
            raise MCPProtocolError(f"MCP server '{server}' startup was cancelled")
        if not task.result():
            raise MCPProtocolError(
                f"unknown or unavailable MCP server: {server}; "
                + self.initial_failures.get(server, "startup failed")
            )
        if connection := self.staged.get(server):
            return connection
        raise KeyError(f"unknown or unavailable MCP server: {server}")

    @property
    def completed_count(self) -> int:
        return sum(task.done() for task in self.tasks.values())
