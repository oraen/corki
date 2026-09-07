"""Transactional MCP generations, explicit refresh, and connection ownership."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import ExitStack, suppress
from copy import deepcopy

from corki.config import MCPServerSettings
from corki.mcp.client import create_client
from corki.mcp.connection import MCPConnection, MCPServerMetadata
from corki.mcp.resources import resource_tools
from corki.mcp.tools import MCPTool
from corki.protocol.tools import ToolExposure
from corki.tools import ToolRegistry


class MCPManager:
    def __init__(
        self,
        settings: tuple[MCPServerSettings, ...],
        registry: ToolRegistry,
        *,
        defer_tools: bool = False,
        server_metadata: Mapping[str, MCPServerMetadata] | None = None,
    ) -> None:
        if len({s.name for s in settings}) != len(settings):
            raise ValueError("MCP server names must be unique")
        self._settings = deepcopy(settings)
        self._server_metadata = dict(server_metadata or {})
        self._registry = registry
        self._owner = registry.create_owner()
        self._defer_tools = defer_tools
        self._clients_by_name: dict[str, MCPConnection] = {}
        self._retired: set[MCPConnection] = set()
        self._close_error: BaseException | None = None
        self._shutdown_task: asyncio.Task | None = None
        self._gate = asyncio.Lock()
        self._pending = True
        self._closed = False
        self._started = False
        self._aggregate_tools = resource_tools(self)
        self.tool_names: tuple[str, ...] = ()
        self.warnings: tuple[str, ...] = ()

    @property
    def refresh_pending(self) -> bool:
        return self._pending

    def request_refresh(
        self,
        settings: tuple[MCPServerSettings, ...] | None = None,
        *,
        server_metadata: Mapping[str, MCPServerMetadata] | None = None,
    ) -> None:
        """Invalidate explicitly; never infer permission to replay a failed tools/call."""
        if self._closed:
            raise RuntimeError("MCP manager is closed")
        if settings is not None:
            if len({s.name for s in settings}) != len(settings):
                raise ValueError("MCP server names must be unique")
            self._settings = deepcopy(settings)
        if server_metadata is not None:
            self._server_metadata = dict(server_metadata)
        self._pending = True

    async def start(self) -> None:
        if not self._started:
            await self.refresh_if_dirty()

    async def refresh_if_dirty(self) -> None:
        async with self._gate:
            if self._closed:
                raise RuntimeError("MCP manager is closed")
            while self._pending:
                self._pending = False
                staged: dict[str, MCPConnection] = {}
                tools: list = []
                warnings: list[str] = []
                current: MCPConnection | None = None
                try:
                    owned = self._registry.owned_names(self._owner)
                    names: set[str] = set()
                    # Capture desired settings. An invalidation during an await
                    # remains pending and is consumed by the next loop iteration.
                    metadata = self._server_metadata.copy()
                    for settings in self._settings:
                        try:
                            current = MCPConnection(
                                create_client(settings),
                                self._connection_closed,
                                metadata=metadata.get(settings.name, MCPServerMetadata()),
                            )
                            await current.client.start()
                            definitions = await current.client.list_tools()
                            candidates = tuple(
                                MCPTool(
                                    settings.name,
                                    definition,
                                    current,
                                    exposure=ToolExposure.DEFERRED
                                    if self._defer_tools
                                    else ToolExposure.DIRECT,
                                    call_router=self.call_tool,
                                    server_instructions=current.client.server_instructions,
                                )
                                for definition in definitions
                            )
                            exposed = [tool.spec.name for tool in candidates]
                            current.remote_tools = frozenset(
                                tool.remote_name for tool in candidates
                            )
                            if len(set(exposed)) != len(exposed):
                                raise ValueError(
                                    "MCP server exposed duplicate normalized tool names"
                                )
                            if any(
                                name in names
                                or (self._registry.get(name) is not None and name not in owned)
                                for name in exposed
                            ):
                                raise ValueError("MCP tool already registered")
                            staged[settings.name] = current
                            names.update(exposed)
                            tools.extend(candidates)
                            current = None
                        except Exception as exc:
                            warnings.append(f"{settings.name}: {type(exc).__name__}: {exc}"[:2000])
                            if current is not None:
                                self._retired.add(current)
                                with suppress(Exception):
                                    await current.aclose()
                            current = None
                    if staged:
                        for tool in self._aggregate_tools:
                            name = tool.spec.name
                            if self._registry.get(name) is not None and name not in owned:
                                warnings.append(f"aggregate MCP tool already registered: {name}")
                            else:
                                tools.append(tool)
                    # No suspension between registry and manager publication.
                    # Validation/deepcopy in replace_owned finishes before it swaps.
                    if self._closed:
                        raise RuntimeError("MCP manager closed during refresh")
                    tool_names = tuple(tool.spec.name for tool in tools)
                    self._registry.replace_owned(self._owner, tuple(tools))
                    previous = tuple(self._clients_by_name.values())
                    self._clients_by_name = staged
                    self.tool_names = tool_names
                    self.warnings = tuple(warnings[:32])
                    self._started = True
                    for connection in previous:
                        self._retired.add(connection)
                        connection.retire()
                except BaseException:
                    # Restore the claimed invalidation even on cancellation.
                    self._pending = True
                    unpublished = list(staged.values())
                    if current is not None:
                        unpublished.append(current)
                    for connection in unpublished:
                        if not connection.closed:
                            self._retired.add(connection)
                        with suppress(BaseException):
                            await connection.aclose()
                    raise

    def _connection_closed(self, connection, error) -> None:
        self._retired.discard(connection)
        if error is not None:
            self._close_error = self._close_error or error

    @property
    def prompt_inventory(self) -> str:
        """Bounded direct names and deferred sources, without an eager tool catalog."""
        names = set(self.tool_names)
        specs = tuple(spec for spec in self._registry.specs() if spec.name in names)
        direct = [f"- {spec.name}" for spec in specs if spec.exposure.is_model_visible]
        sources = sorted(
            {spec.source for spec in specs if spec.exposure.is_deferred and spec.source}
        )
        if sources:
            direct.append("Deferred sources: " + ", ".join(sources))
            direct.append("Use tool_search to discover their tools and load callable definitions.")
        return "\n".join(direct)[:8_000] or "- none"

    async def aclose(self) -> None:
        if self._shutdown_task is None:
            self._closed = True
            self._shutdown_task = asyncio.create_task(self._shutdown(), name="mcp-shutdown")
        await asyncio.shield(self._shutdown_task)

    async def _shutdown(self) -> None:
        async with self._gate:
            connections = (*self._clients_by_name.values(), *self._retired)
            for connection in connections:
                try:
                    await connection.aclose()
                except BaseException as exc:
                    self._close_error = self._close_error or exc
            self._clients_by_name.clear()
            self._retired.clear()
            if self._close_error is not None:
                raise self._close_error

    async def list_capability(self, kind: str, server: str | None) -> dict[str, object]:
        await self.refresh_if_dirty()
        methods = {
            "resources": "list_resources",
            "templates": "list_resource_templates",
            "prompts": "list_prompts",
        }
        if kind not in methods:
            raise ValueError(f"unknown MCP capability: {kind}")
        clients = (
            ((server, self._client(server)),)
            if server is not None
            else tuple(self._clients_by_name.items())
        )
        output = {}
        with ExitStack() as leases:
            captured = tuple(
                (name, leases.enter_context(client.lease())) for name, client in clients
            )
            for name, client in captured:
                output[name] = await getattr(client, methods[kind])()
        return output

    async def read_resource(self, server: str, uri: str):
        await self.refresh_if_dirty()
        return await self._client(server).invoke("read_resource", uri)

    async def get_prompt(self, server: str, name: str, arguments: dict[str, str]):
        await self.refresh_if_dirty()
        return await self._client(server).invoke("get_prompt", name, arguments)

    async def call_tool(
        self,
        server: str,
        name: str,
        arguments,
        *,
        on_external_context=None,
        on_output_token_limit=None,
    ):
        # Codex prepares a call from the latest binding, not the client attached
        # to the model's older schema. Only an admitted call retains its client.
        await self.refresh_if_dirty()
        connection = self._client(server)
        if name not in connection.remote_tools:
            raise KeyError(f"unknown MCP tool: {server}/{name}")
        return await connection.call_tool(
            name,
            arguments,
            on_external_context=on_external_context,
            on_output_token_limit=on_output_token_limit,
        )

    def _client(self, server: str) -> MCPConnection:
        client = self._clients_by_name.get(server)
        if client is None:
            raise KeyError(f"unknown MCP server: {server}")
        return client
