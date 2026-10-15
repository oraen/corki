"""Transport identity and explicit ownership shared by immutable MCP views."""

import asyncio
import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path

from corki.config import MCPEnvVar, MCPServerSettings
from corki.mcp.catalog_policy import REGULAR_CATALOG_LIMIT, catalog_limit, validate_catalog_limit
from corki.mcp.client import MCPClient, MCPProtocolError
from corki.mcp.runtime_environment import MCPHTTPEnvironment
from corki.mcp.tool_catalog_cache import CatalogCacheContext, CatalogSnapshot
from corki.mcp.tool_definition import validated_tool_fields


def connection_identity(settings: MCPServerSettings) -> str:
    """Hash transport config and named ambient references, excluding per-view policy."""
    if settings.transport == "http":
        names = {value for _, value in settings.env_http_headers or ()}
        if settings.bearer_token_env_var is not None:
            names.add(settings.bearer_token_env_var)
        inputs = (
            "http",
            settings.url,
            sorted(dict(settings.headers).items()),
            None if settings.http_headers is None else sorted(settings.http_headers),
            None if settings.env_http_headers is None else sorted(settings.env_http_headers),
            settings.bearer_token_env_var,
            settings.http_headers_helper,
            str((settings.cwd or Path.cwd()).resolve())
            if settings.http_headers_helper is not None
            else None,
            tuple((name, os.environ.get(name)) for name in sorted(names)),
        )
    else:
        references = tuple(
            {"name": ref.name, "source": ref.source} if isinstance(ref, MCPEnvVar) else ref
            for ref in settings.env_vars
        )
        names = {
            ref.name if isinstance(ref, MCPEnvVar) else ref
            for ref in settings.env_vars
            if not isinstance(ref, MCPEnvVar) or ref.source != "remote"
        }
        referenced_values = tuple((name, os.environ.get(name)) for name in sorted(names))
        # A direct manager may omit cwd; capture the cwd actually inherited by
        # subprocess creation. Runtime supplies the explicit Turn directory.
        cwd = str((settings.cwd or Path.cwd()).resolve())
        inputs = (
            "stdio",
            settings.command,
            settings.args,
            cwd,
            sorted(dict(settings.env).items()),
            references,
            referenced_values,
        )
    return hashlib.sha256(
        json.dumps((settings.environment_id, *inputs), ensure_ascii=True).encode()
    ).hexdigest()


class MCPTransportOwner:
    """Close one physical client only after every owning view has released it."""

    def __init__(
        self,
        client: MCPClient,
        settings: MCPServerSettings | None,
        environment: MCPHTTPEnvironment | None = None,
        agent_plugin: bool = False,
        cache_context: CatalogCacheContext | None = None,
        *,
        catalog_item_limit: int = REGULAR_CATALOG_LIMIT,
    ) -> None:
        validate_catalog_limit(catalog_item_limit)
        self.catalog_item_limit = catalog_item_limit
        self.client = client
        self._settings = settings
        self._environment = environment
        self._agent_plugin = agent_plugin
        self._cache_context = cache_context
        self.identity: str | None = None
        self.server_instructions: str | None = None
        self._definitions = None
        self._references = 0
        self._startup_task: asyncio.Task | None = None
        self._close_task: asyncio.Task | None = None

    def retain(self) -> None:
        """Acquire one view reference before it can be staged or published."""
        if self._close_task is not None:
            raise RuntimeError("MCP transport is closing")
        self._references += 1

    async def release(self) -> None:
        """Release one view, joining actual close only for the final owner."""
        self._references -= 1
        assert self._references >= 0
        if self._references == 0:
            self._close_task = asyncio.create_task(self._close(), name="mcp-transport-close")
            await asyncio.shield(self._close_task)

    async def start(self) -> None:
        """Borrow one startup; view cancellation is separate from an explicit host stop."""
        if self._startup_task is None:
            self._startup_task = asyncio.create_task(self._initialize(), name="mcp-transport-start")
            self._startup_task.add_done_callback(
                lambda task: None if task.cancelled() else task.exception()
            )
        await asyncio.shield(self._startup_task)

    async def _close(self) -> None:
        startup = self._startup_task
        if startup is not None:
            if not startup.done() and not startup.cancelling():
                startup.cancel()
            await asyncio.gather(startup, return_exceptions=True)
        await self.client.aclose()

    @property
    def startup_pending(self) -> bool:
        task = self._startup_task
        return (
            task is not None
            and not task.done()
            and not task.cancelling()
            and self._close_task is None
        )

    def cancel_startup(self) -> None:
        """An explicit host stop targets physical work, regardless of borrowed views."""
        task = self._startup_task
        if task is not None and not task.done() and not task.cancelling():
            task.cancel()

    def can_reuse_pending(
        self,
        settings,
        environment=None,
        *,
        agent_plugin=False,
        catalog_item_limit=REGULAR_CATALOG_LIMIT,
    ) -> bool:
        """Pending reuse additionally requires the same startup budget."""
        return (
            self.startup_pending
            and self._settings is not None
            and self._settings.timeout_seconds == settings.timeout_seconds
            and getattr(self.client, "is_closed", False) is False
            and self._environment is environment
            and self._agent_plugin == agent_plugin
            and self.catalog_item_limit == catalog_item_limit
            and self.identity == connection_identity(settings)
        )

    async def _initialize(self) -> None:
        """Capture connection identity and the complete initialized catalog once."""
        # Compute potentially fallible identity only after the view owns cleanup.
        self.identity = connection_identity(self._settings) if self._settings is not None else None
        cache = self._cache_context
        ticket = cache.begin_fetch() if cache is not None else None
        await self.client.start()
        if cache is not None and getattr(self.client, "tool_catalog_cacheable", True) is False:
            cache.disable()
        with catalog_limit(self.client, self.catalog_item_limit):
            definitions = deepcopy(tuple(await self.client.list_tools()))
        # Custom client implementations must obey the same host capacity before caching.
        if len(definitions) > self.catalog_item_limit:
            raise MCPProtocolError(
                f"tools/list exceeded the catalog limit of {self.catalog_item_limit} items"
            )
        for definition in definitions:
            validated_tool_fields(definition)
        self._definitions = definitions
        self.server_instructions = self.client.server_instructions
        if cache is not None and ticket is not None:
            if getattr(self.client, "tool_catalog_cacheable", True) is False:
                cache.disable()
            cache.publish_if_newest(
                ticket, CatalogSnapshot(self._definitions, self.server_instructions)
            )

    @property
    def definitions(self) -> tuple:
        """Return isolated raw catalog entries, including currently filtered tools."""
        if self._definitions is None:
            raise RuntimeError("MCP catalog has not completed startup")
        return deepcopy(self._definitions)

    def can_reuse(
        self,
        settings: MCPServerSettings,
        environment: MCPHTTPEnvironment | None = None,
        *,
        agent_plugin: bool = False,
        catalog_item_limit: int = REGULAR_CATALOG_LIMIT,
    ) -> bool:
        """Compare transport inputs only after complete startup and a liveness check."""
        return (
            self._definitions is not None
            and self._close_task is None
            and getattr(self.client, "is_closed", False) is False
            and self._environment is environment
            and self._agent_plugin == agent_plugin
            and self.catalog_item_limit == catalog_item_limit
            and self.identity == connection_identity(settings)
        )
