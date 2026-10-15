"""Transactional MCP generations, explicit refresh, and connection ownership."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import ExitStack, asynccontextmanager, suppress
from copy import deepcopy
from functools import partial
from time import monotonic

from corki.config import MCPServerSettings
from corki.config.mcp_requirements import MCPRequirements, MCPServerSource
from corki.mcp.admission import MCPDisabledError, MCPUnavailableError
from corki.mcp.approval_persistence import MCPApprovalPersistence, approval_settings
from corki.mcp.approvals import MCPToolAnnotations, MCPToolApprovals
from corki.mcp.catalog import MCPCatalog, MCPRegistration
from corki.mcp.catalog_policy import REGULAR_CATALOG_LIMIT
from corki.mcp.client import HttpMCPClient, MCPProtocolError, create_client
from corki.mcp.connection import MCPConnection, MCPServerMetadata
from corki.mcp.discovery import MCPToolCatalogEntry
from corki.mcp.elicitation import ElicitationRouter
from corki.mcp.exposure import apply_agent_budget
from corki.mcp.input_schema import MCPInputSchemaError
from corki.mcp.model_access import ensure_model_access
from corki.mcp.names import normalize_tool_names
from corki.mcp.prepared_call import MCPPreparedCall
from corki.mcp.projection import project_tool, raw_call_bindings
from corki.mcp.resource_binding import MCPResourceBinding
from corki.mcp.resource_inventory import aggregate_resources
from corki.mcp.resources import resource_tools
from corki.mcp.runtime_environment import MCPRuntimeContext
from corki.mcp.startup import MCPPreparation
from corki.mcp.startup_handoff import PendingStartupClaims
from corki.mcp.tool_catalog_cache import (
    SHARED_TOOL_CATALOG_CACHE,
    CatalogSnapshot,
    MCPToolCatalogCache,
)
from corki.mcp.tool_definition import tool_is_model_visible
from corki.mcp.tool_filter import MCPToolFilter
from corki.mcp.tools import MCPTool
from corki.protocol.tools import ToolExposure
from corki.tools import ToolRegistry, ToolSource


class MCPManager:
    def __init__(
        self,
        settings: tuple[MCPServerSettings, ...],
        registry: ToolRegistry,
        *,
        defer_tools: bool = False,
        code_mode_only: bool = False,
        prefix_tool_names: bool = True,
        non_prefixed_servers: tuple[str, ...] = (),
        server_metadata: Mapping[str, MCPServerMetadata] | None = None,
        approval_policy: str = "never",
        auth_elicitation: bool = True,
        plugins_enabled: bool = True,
        approval_persistence: MCPApprovalPersistence | None = None,
        tool_call_elicitation: bool = True,
        requirements: MCPRequirements | Mapping[str, object] | None = None,
        server_sources: Mapping[str, MCPServerSource] | None = None,
        catalog: MCPCatalog | None = None,
        runtime_context: MCPRuntimeContext | None = None,
        oauth_file_home=None,
        oauth_home=None,
        oauth_store_mode="file",
        tool_catalog_cache: MCPToolCatalogCache | None = None,
        lazy_when_cached: bool = False,
    ) -> None:
        if len({s.name for s in settings}) != len(settings):
            raise ValueError("MCP server names must be unique")
        self._requirements = MCPRequirements.coerce(requirements)
        if type(plugins_enabled) is not bool:
            raise ValueError("MCP plugin enablement must be boolean")
        self._plugins_enabled = plugins_enabled
        if type(lazy_when_cached) is not bool:
            raise ValueError("MCP startup policy must be host-owned")
        self._lazy_when_cached = lazy_when_cached
        self._runtime_context = MCPRuntimeContext.coerce(runtime_context)
        self._oauth_file_home = oauth_file_home
        self._oauth_home = oauth_home
        self._oauth_store_mode = oauth_store_mode
        self._tool_catalog_cache = (
            tool_catalog_cache if tool_catalog_cache is not None else SHARED_TOOL_CATALOG_CACHE
        )
        if catalog is not None and not isinstance(catalog, MCPCatalog):
            raise ValueError("MCP catalog must be host-owned typed declarations")
        self._catalog = deepcopy(catalog)
        # Reload must start from declarations, never from an already restricted
        # plugin view. Explicit host replacements also replace this baseline.
        self._approval_catalog = deepcopy(catalog)
        self._published_catalog: MCPCatalog | None = None
        self._server_sources = dict(server_sources or {})
        if any(not isinstance(source, MCPServerSource) for source in self._server_sources.values()):
            raise ValueError("MCP server sources must be host-owned typed identities")
        self._denied_servers: dict[str, str] = {}
        self._settings = deepcopy(settings)
        self.elicitations = ElicitationRouter()
        self._approvals = MCPToolApprovals(approval_policy, self.elicitations)
        if approval_persistence is not None and not isinstance(
            approval_persistence, MCPApprovalPersistence
        ):
            raise ValueError("MCP persistence must be host-owned")
        if type(tool_call_elicitation) is not bool:
            raise ValueError("MCP tool elicitation feature must be boolean")
        self._tool_call_elicitation = tool_call_elicitation
        self._approval_persistence = approval_persistence
        self._approval_document = deepcopy(
            approval_persistence.document if approval_persistence else {}
        )
        if approval_persistence is not None:
            approval_persistence.prepare_reload = self._prepare_approval_configuration
            if self._catalog is not None and self._plugins_enabled:
                self._catalog = self._catalog.transform_settings(
                    lambda entry: approval_settings(
                        entry.settings, self._approval_document, entry.source, plugin_only=True
                    )
                )
        self._approval_policy = approval_policy
        self._server_metadata = dict(server_metadata or {})
        self._registry = registry
        self._owner = registry.create_owner(source=ToolSource.MCP)
        self._defer_tools = defer_tools
        self._code_mode_only = code_mode_only
        self._prefix_tool_names = prefix_tool_names
        self._non_prefixed_servers = non_prefixed_servers
        self._clients_by_name: dict[str, MCPConnection] = {}
        self._retired: set[MCPConnection] = set()
        self._close_error: BaseException | None = None
        self._shutdown_task: asyncio.Task | None = None
        self._gate = asyncio.Lock()
        self._pending = True
        # Initial creation reconciles against an empty view; only explicit
        # refresh requests discard the previous view and force eager startup.
        self._force_reconnect = False
        self._closed = False
        self._started = False
        self._preparation: MCPPreparation | None = None
        self._bound_preparations: dict[int, MCPPreparation] = {}
        self._resource_bindings: set[MCPResourceBinding] = set()
        self._startup_claims: PendingStartupClaims | None = None
        self._required_startup_failures: tuple[str, ...] = ()
        self._aggregate_tools = resource_tools(self)
        self.tool_names: tuple[str, ...] = ()
        self.warnings: tuple[str, ...] = ()

    @property
    def refresh_pending(self) -> bool:
        return self._pending

    @property
    def catalog(self) -> MCPCatalog | None:
        """Return a detached published catalog (or initial desired view before startup)."""
        catalog = self._published_catalog or self._catalog
        if catalog is not None and not self._plugins_enabled:
            catalog = catalog.without_plugins()
        return deepcopy(catalog)

    def request_runtime_context(self, context: MCPRuntimeContext) -> None:
        """Queue concrete host handles; existing admitted calls retain their old views."""
        if self._closed:
            raise RuntimeError("MCP manager is closed")
        self._runtime_context = MCPRuntimeContext.coerce(context)
        self._pending = True

    def request_catalog(self, catalog: MCPCatalog) -> None:
        """Queue host source replacement, distinct from same-source transport materialization."""
        if self._closed:
            raise RuntimeError("MCP manager is closed")
        if not isinstance(catalog, MCPCatalog):
            raise ValueError("MCP catalog must be host-owned typed declarations")
        self._catalog = deepcopy(catalog)
        self._approval_catalog = deepcopy(catalog)
        self._pending = True

    def _prepare_approval_configuration(self, configuration, document, *, catalog=None):
        if self._closed:
            raise RuntimeError("MCP manager is closed")
        declarations = self._approval_catalog if catalog is None else deepcopy(catalog)
        catalog = declarations
        if catalog is not None and self._plugins_enabled:
            catalog = catalog.transform_settings(
                lambda entry: approval_settings(
                    entry.settings, document, entry.source, plugin_only=True
                )
            )
        # Legacy file reload refreshes layer-backed plugin policy. Ordinary
        # servers retain their materialized settings until an explicit host update.
        document = deepcopy(document)

        def publish():
            self._approval_catalog = declarations
            self._approval_document = document
            self._catalog = catalog
            self._pending = True

        return publish

    def request_refresh(
        self,
        settings: tuple[MCPServerSettings, ...] | None = None,
        *,
        server_metadata: Mapping[str, MCPServerMetadata] | None = None,
    ) -> None:
        """Invalidate explicitly; never infer permission to replay a failed tools/call."""
        self.request_reconcile(settings, server_metadata=server_metadata)
        self._force_reconnect = True

    def skill_dependency_catalog(self, candidates=()):
        """Preview candidates through the same controller and owner admission as startup."""
        catalog = self._catalog or MCPCatalog(tuple(MCPRegistration(s) for s in self._settings))
        if not self._plugins_enabled:
            catalog = catalog.without_plugins()
        names = {entry.name for entry in catalog.servers}
        catalog = catalog.extend(*(MCPRegistration(s) for s in candidates if s.name not in names))
        return catalog.constrain(self._requirements).constrain_environments(
            self._runtime_context.requirements_for
        )

    def publish_skill_dependencies(self, servers):
        """Add host-approved declarations without replacing hidden sources or active calls."""
        if self._closed:
            raise RuntimeError("MCP manager is closed")
        catalog = self._catalog or MCPCatalog(tuple(MCPRegistration(s) for s in self._settings))
        names = {entry.name for entry in catalog.servers}
        additions = tuple(MCPRegistration(s) for s in servers if s.name not in names)
        self._catalog = catalog.extend(*additions)
        self._approval_catalog = self._catalog
        self._pending = True
        return additions

    def request_reconcile(
        self,
        settings: tuple[MCPServerSettings, ...] | None = None,
        *,
        server_metadata: Mapping[str, MCPServerMetadata] | None = None,
    ) -> None:
        """Queue new views, reusing healthy transports unless a reconnect is pending."""
        if self._closed:
            raise RuntimeError("MCP manager is closed")
        if settings is not None:
            if len({s.name for s in settings}) != len(settings):
                raise ValueError("MCP server names must be unique")
            self._settings = deepcopy(settings)
            if self._catalog is not None:
                self._catalog = self._catalog.materialize(settings)
                self._approval_catalog = deepcopy(self._catalog)
        if server_metadata is not None:
            self._server_metadata = dict(server_metadata)
        self._pending = True

    async def start(self) -> None:
        """Explicit host startup waits for the complete catalog."""
        failures = await self._capture("all", cancel_on_abort=True, validate_required=True)
        self._validate_required(failures)

    async def start_session(self) -> None:
        """Admit a session after required servers, retaining optional startup."""
        failures = await self._capture("required", cancel_on_abort=True, validate_required=True)
        self._validate_required(failures)

    def _validate_required(self, failures: tuple[str, ...]) -> None:
        if failures:
            self._pending = True
            raise RuntimeError("required MCP servers failed to initialize: " + "; ".join(failures))

    async def refresh_if_dirty(self) -> None:
        """Explicit host/catalog operations request complete readiness."""
        await self._capture("all", cancel_on_abort=True)

    async def publish_pending(self) -> None:
        """Best-effort prewarm publishes desired state, never waits for tool readiness."""
        if self._pending:
            await self._capture("publish", cancel_on_abort=True)

    async def capture_tools(
        self, *, optional_startup_grace_ms: int = 1000, required_servers=(), required_plugins=()
    ) -> None:
        """Capture ready/cached Step definitions without cancelling optional work."""
        await self._capture(
            "step",
            grace_ms=optional_startup_grace_ms,
            required_servers=required_servers,
            required_plugins=required_plugins,
        )

    async def prepare_server(self, server: str) -> None:
        """Wait for the named server, independently of unrelated optional startup."""
        await self._capture("server", server=server)

    async def list_tool_catalog(self) -> tuple[MCPToolCatalogEntry, ...]:
        """Inspect current tool metadata without granting execution authority."""
        await self._capture("discovery")
        generation = self._preparation
        snapshots = {}
        for settings in generation.desired:
            name = settings.name
            if not settings.enabled or name in generation.denied:
                continue
            snapshot = None
            connection = generation.staged.get(name)
            if snapshot is None and connection is not None:
                snapshot = CatalogSnapshot(connection.definitions, connection.server_instructions)
            if snapshot is None and not generation.tasks[name].done():
                cache = generation.catalog_contexts.get(name)
                snapshot = cache.current_tools_or() if cache is not None else None
            if snapshot is not None:
                snapshots[name] = snapshot
        return self._catalog_entries(generation, snapshots)

    def _catalog_entries(self, generation, snapshots):
        sources = (
            {s.name: s.source for s in generation.catalog.servers} if generation.catalog else {}
        )
        candidates = {}
        for settings in generation.desired:
            name = settings.name
            if not settings.enabled or name in generation.denied or name not in snapshots:
                continue
            snapshot = snapshots[name]
            tool_filter = MCPToolFilter.from_settings(settings)
            for definition in snapshot.definitions:
                if tool_filter.allows(definition["name"]):
                    projection = project_tool(name, definition, snapshot.instructions)
                    candidates.setdefault(
                        projection.identity,
                        (definition, snapshot.instructions, projection),
                    )
        names = normalize_tool_names(
            candidates,
            prefix=self._prefix_tool_names,
            non_prefixed_servers=self._non_prefixed_servers,
        )
        return tuple(
            MCPToolCatalogEntry(
                name.canonical,
                name.server,
                deepcopy(candidates[name.identity][0]),
                candidates[name.identity][1],
                deepcopy(sources.get(name.server)),
                deepcopy(generation.server_metadata.get(name.server, MCPServerMetadata())),
                name.identity.connector_id,
                candidates[name.identity][2].connector_name,
                candidates[name.identity][2].namespace_description,
            )
            for name in names
        )

    @asynccontextmanager
    async def _selected_generation(self):
        """Own dispatch's current set before any server-readiness wait."""
        async with self._gate:
            if self._closed:
                raise RuntimeError("MCP manager is closed")
            try:
                await self._prepare_generation()
            except BaseException as exc:
                await self._abort_capture(self._preparation, exc, False)
                raise
            generation = self._preparation
            generation.binding_users += 1
            self._bound_preparations[id(generation)] = generation
        try:
            yield generation
        finally:
            if cleanup := self._release_resource_binding(generation):
                await asyncio.shield(cleanup)

    async def _wait_selected_client(self, generation, server):
        try:
            return await generation.wait_for_client(
                server, waiter=lambda: self._wait_preparation(generation, "server", 1000, server)
            )
        except (KeyError, MCPProtocolError):
            # Failed readiness must still revoke the prior publication's clients
            # and tools. Never publish an obsolete generation or swallow caller
            # cancellation; the selected startup's failure remains the outcome.
            async with generation.catalog_revision.read(), self._gate:
                if not self._closed and generation is self._preparation and not self._pending:
                    self._publish_generation(generation, {})
            raise

    def capture_resource_binding(self) -> MCPResourceBinding:
        """Capture synchronously with the Step router, without refreshing readiness."""
        if self._closed:
            raise RuntimeError("MCP manager is closed")
        generation = self._preparation
        if generation is not None:
            generation.binding_users += 1
            self._bound_preparations[id(generation)] = generation
        # A failed refresh may leave the last publication without a current
        # preparation. Ready views therefore own their transport independently.
        ready = {
            name: connection.fork(connection.settings, connection.metadata)
            for name, connection in self._clients_by_name.items()
        }
        self._retired.update(ready.values())
        binding = MCPResourceBinding(generation, ready, self._release_resource_binding)
        self._resource_bindings.add(binding)
        binding.closed.add_done_callback(
            lambda future: self._resource_binding_closed(binding, future)
        )
        return binding

    def _resource_binding_closed(self, binding, future):
        # Background cells may release after their original Turn has completed.
        self._resource_bindings.discard(binding)
        try:
            future.result()
        except BaseException as exc:
            self._close_error = self._close_error or exc

    def _release_resource_binding(self, generation):
        if generation is None:
            return None
        generation.binding_users -= 1
        assert generation.binding_users >= 0
        if generation.binding_users:
            return None
        self._bound_preparations.pop(id(generation), None)
        if generation is not self._preparation:
            return self._close_preparation(generation)
        return None

    def cancel_startup(self) -> None:
        """Cancel started preparations; leave dormant and established connections alone."""
        if self._startup_claims is not None:
            self._startup_claims.cancel_startup()
        if self._preparation is not None:
            for name, task in self._preparation.tasks.items():
                if (
                    not self._preparation.is_dormant(name)
                    and not task.done()
                    and not task.cancelling()
                ):
                    connection = self._preparation.pending_connections.get(name)
                    if connection is not None:
                        if not connection.startup_pending:
                            continue
                        connection.cancel_startup()
                    task.cancel()

    async def rollback_startup(self) -> None:
        """Join unpublished startup when session initialization itself fails."""
        async with self._gate:
            generation = self._preparation
            await self._dispose_preparation()
            self._pending = True
            if generation is not None and generation.published_count < 0:
                self._force_reconnect = self._force_reconnect or generation.force_reconnect

    async def _capture(
        self,
        mode: str,
        *,
        grace_ms: int = 1000,
        server: str | None = None,
        cancel_on_abort: bool = False,
        required_servers=(),
        required_plugins=(),
        validate_required: bool = False,
    ) -> tuple[str, ...]:
        while True:
            async with self._gate:
                if self._closed:
                    raise RuntimeError("MCP manager is closed")
                try:
                    await self._prepare_generation()
                except BaseException as exc:
                    await self._abort_capture(self._preparation, exc, cancel_on_abort)
                    raise
                generation = self._preparation
            try:
                # Exact readiness must not lock out publication of a newer config.
                failures: list[str] = []
                async with generation.catalog_revision.read():
                    fallbacks = await self._wait_preparation(
                        generation,
                        mode,
                        grace_ms,
                        server,
                        required_servers,
                        required_plugins,
                        failures if validate_required else None,
                    )
                    async with self._gate:
                        if generation is not self._preparation or self._pending:
                            continue
                        self._publish_generation(generation, fallbacks)
                        return tuple(failures)
            except BaseException as exc:
                async with self._gate:
                    await self._abort_capture(generation, exc, cancel_on_abort)
                raise

    async def _abort_capture(self, generation, error, cancel_on_abort) -> None:
        """Caller holds publication gate; never roll back a successor's claim."""
        if generation is self._preparation and (
            not isinstance(error, asyncio.CancelledError)
            or cancel_on_abort
            and (generation is None or generation.published_count < 0)
        ):
            try:
                await self._dispose_preparation()
            finally:
                self._pending = True
                if generation is not None and generation.published_count < 0:
                    self._force_reconnect = self._force_reconnect or generation.force_reconnect

    async def _prepare_generation(self) -> None:
        """Replace physical ownership while holding the single publication gate."""
        if not self._pending and self._preparation is not None:
            return
        claims = PendingStartupClaims(self._preparation, force_reconnect=self._force_reconnect)
        self._startup_claims = claims
        force_reconnect = False
        try:
            await self._dispose_preparation()
            if self._closed:
                raise RuntimeError("MCP manager is closed")
            force_reconnect = self._force_reconnect
            self._preparation = self._begin_generation(claims.views)
            # Successor tasks acquire their view before temporary claims release.
            await asyncio.sleep(0)
        except BaseException:
            self._pending = True
            self._force_reconnect = self._force_reconnect or force_reconnect
            raise
        finally:
            try:
                await claims.release()
            finally:
                self._startup_claims = None

    async def _wait_preparation(
        self,
        generation: MCPPreparation,
        mode: str,
        grace_ms: int,
        server: str | None,
        required_servers=(),
        required_plugins=(),
        validation_failures: list[str] | None = None,
    ) -> dict:
        async def wait(tasks, timeout=None):
            pending = [task for task in tasks if not task.done()]
            if pending:
                await asyncio.wait(pending, timeout=timeout, return_when=asyncio.FIRST_EXCEPTION)
            for task in tasks:
                if task.done() and not task.cancelled():
                    task.result()

        # Let each task resolve its environment and cache identity before capturing.
        # No server I/O needs to finish at this boundary.
        await asyncio.sleep(0)
        fallbacks = {
            name: snapshot
            for name, cache in generation.catalog_contexts.items()
            if (snapshot := cache.current_tools_or()) is not None
            and (mode == "discovery" or snapshot.definitions)
        }
        if mode == "publish":
            return fallbacks
        selected = (
            {
                s.name
                for s in generation.catalog.servers
                if s.source.kind == "selected_plugin" and s.source.identity in required_plugins
            }
            if generation.catalog is not None
            else set()
        )
        required = [
            generation.tasks[s.name]
            for s in generation.desired
            if s.enabled
            and (
                s.name in required_servers
                or s.name in selected
                or s.required
                and (not generation.is_dormant(s.name) or s.name not in fallbacks)
            )
        ]
        for name, task in generation.tasks.items():
            if (
                mode != "discovery"
                and task in required
                or mode == "all"
                or mode == "server"
                and name == server
                or mode == "discovery"
                and name not in fallbacks
            ):
                generation.activate(name)
        if validation_failures is not None:
            # Match native sequential client() lookup, not a batch wait followed
            # by a mutable failure snapshot. Once an original startup is selected,
            # recovery during its await cannot replace that original outcome.
            for settings in sorted(generation.desired, key=lambda settings: settings.name):
                name = settings.name
                task = generation.tasks[name]
                if (
                    not settings.enabled
                    or not settings.required
                    or mode != "all"
                    and task not in required
                ):
                    continue
                if name in generation.staged:
                    continue
                await wait([task])
                if name in generation.denied:
                    continue
                if task.cancelled() or not task.result():
                    validation_failures.append(
                        generation.initial_failures.get(
                            name, f"{name}: required MCP server was not initialized"
                        )
                    )
        if mode == "all":
            await wait(list(generation.tasks.values()))
        elif mode == "server":
            await wait([generation.tasks[server]] if server in generation.tasks else [])
        elif mode == "required":
            await wait(required)
        elif mode == "discovery":
            await wait([task for name, task in generation.tasks.items() if name not in fallbacks])
        else:
            if generation.deadline is None:
                generation.deadline = monotonic() + grace_ms / 1000
            deadlines = {
                name: cache.optional_startup_deadline(
                    monotonic() + grace_ms / 1000
                    if generation.is_dormant(name)
                    else generation.deadline,
                    grace_ms / 1000,
                )
                for name, cache in generation.catalog_contexts.items()
                if name not in fallbacks
            }
            await wait(required)

            async def wait_optional(name, task):
                if name in fallbacks:
                    return
                generation.activate(name)
                timeout = (
                    None
                    if grace_ms == 0
                    else max(0, deadlines.get(name, generation.deadline) - monotonic())
                )
                await wait([task], timeout)

            await asyncio.gather(
                *(wait_optional(name, task) for name, task in generation.tasks.items())
            )
        return fallbacks

    async def _dispose_preparation(self) -> None:
        generation, self._preparation = self._preparation, None
        if generation is None:
            return
        if generation.binding_users:
            # Old Steps may still need this exact generation's dormant/pending
            # named fallback. The last binding owns its eventual disposal.
            return
        cleanup = self._close_preparation(generation)
        interrupted = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                interrupted = True
        cleanup.result()
        if interrupted:
            raise asyncio.CancelledError

    def _close_preparation(self, generation):
        if generation.cleanup_task is not None:
            return generation.cleanup_task

        async def dispose():
            for task in generation.tasks.values():
                if not task.done() and not task.cancelling():
                    task.cancel()
            await asyncio.gather(*generation.tasks.values(), return_exceptions=True)
            published = set(self._clients_by_name.values())
            for connection in generation.owned:
                if connection not in published:
                    if not connection.closed:
                        self._retired.add(connection)
                    with suppress(BaseException):
                        await connection.aclose()

        generation.cleanup_task = asyncio.create_task(dispose(), name="corki-mcp-dispose")
        return generation.cleanup_task

    def _begin_generation(self, pending_views=None) -> MCPPreparation:
        """Publish ownership before asynchronous server preparation begins."""
        self._pending = False
        force_reconnect, self._force_reconnect = self._force_reconnect, False
        staged: dict[str, MCPConnection] = {}
        warnings: list[str] = []
        required_failures: dict[str, str] = {}
        denied: dict[str, str] = {}
        owned: list[MCPConnection] = []
        prepared: dict[str, tuple[MCPTool, ...]] = {}
        server_warnings: dict[str, str] = {}
        # Capture desired settings. An invalidation during an await
        # remains pending and is consumed by the next loop iteration.
        metadata = self._server_metadata.copy()
        runtime_context = self._runtime_context
        # Keep the raw desired catalog: materialization must remember a filtered
        # server's source, otherwise a later refresh can relabel it as config.
        catalog = self._catalog
        if catalog is not None and not self._plugins_enabled:
            catalog = catalog.without_plugins()
        catalog = (
            catalog.constrain(self._requirements).constrain_environments(
                runtime_context.requirements_for
            )
            if catalog is not None
            else None
        )
        desired = (
            tuple(server.settings for server in catalog.servers)
            if catalog is not None
            else tuple(
                server
                for server in self._settings
                if self._plugins_enabled
                or self._server_sources.get(server.name, MCPServerSource(server.name)).plugin_id
                is None
            )
        )
        attributions = (
            {server.name: server.source for server in catalog.servers}
            if catalog is not None
            else {}
        )
        if catalog is not None:
            for conflict in catalog.conflicts:
                warnings.append(
                    (
                        f"{conflict.name}: conflicting MCP source declarations; using "
                        f"{conflict.outcome.source.kind}/{conflict.outcome.source.identity}"
                    )[:2000]
                )

        previous = {**self._clients_by_name, **(pending_views or {})}

        def stage_ready(settings, current):
            self._stage_ready(generation, settings, current)

        async def stage_server(settings: MCPServerSettings) -> bool:
            current: MCPConnection | None = None
            attribution = attributions.get(settings.name)
            catalog_item_limit = REGULAR_CATALOG_LIMIT
            agent_plugin = attribution.agent_plugin if attribution is not None else False
            source = self._server_sources.get(settings.name, MCPServerSource(settings.name))
            owner_allowed = runtime_context.requirements_for(settings.environment_id).allows(
                settings, source
            )
            if (
                not settings.enabled
                or (catalog is None and not self._requirements.allows(settings, source))
                or (catalog is None and not owner_allowed)
            ):
                reason = (
                    settings.disabled_reason or "configuration"
                    if not settings.enabled
                    else "environment requirements"
                    if not owner_allowed
                    else "managed requirements"
                )
                denied[settings.name] = reason
                server_warnings[settings.name] = f"{settings.name}: MCP server disabled by {reason}"
                return False
            try:
                # Resolve before reuse as well as before the client factory.
                # A changed environment must not inherit an older local lease.
                environment = runtime_context.resolve(settings)
                cache = self._tool_catalog_cache.context(
                    settings, environment, agent_plugin=agent_plugin
                )
                if cache is not None:
                    generation.catalog_contexts[settings.name] = cache
                old = previous.get(settings.name)
                policy = metadata.get(settings.name, MCPServerMetadata())
                pending_reuse = (
                    not force_reconnect
                    and old is not None
                    and old.can_reuse_pending(
                        settings,
                        environment,
                        agent_plugin=agent_plugin,
                        catalog_item_limit=catalog_item_limit,
                    )
                )
                if (
                    not force_reconnect
                    and old is not None
                    and (
                        pending_reuse
                        or old.can_reuse(
                            settings,
                            environment,
                            agent_plugin=agent_plugin,
                            catalog_item_limit=catalog_item_limit,
                        )
                    )
                ):
                    current = old.fork(settings, policy)
                    owned.append(current)
                    if pending_reuse:
                        generation.cache_eligible.add(settings.name)
                        generation.pending_connections[settings.name] = current
                        await current.start()
                else:
                    generation.cache_eligible.add(settings.name)
                    cached = cache.current_tools_or() if cache is not None else None
                    tool_filter = MCPToolFilter.from_settings(settings)
                    if (
                        self._lazy_when_cached
                        and not force_reconnect
                        and (attribution is None or attribution.kind != "selected_plugin")
                        and cached is not None
                        and any(
                            tool_filter.allows(d["name"]) and tool_is_model_visible(d)
                            for d in cached.definitions
                        )
                    ):
                        trigger = asyncio.Event()
                        generation.startup_triggers[settings.name] = trigger
                        await trigger.wait()
                    factory = partial(
                        self._new_connection,
                        settings,
                        policy,
                        environment,
                        agent_plugin,
                        cache,
                        catalog_item_limit,
                    )
                    current = factory()
                    # Own every connection before its first await, including
                    # siblings that finish after this generation is cancelled.
                    owned.append(current)
                    generation.pending_connections[settings.name] = current
                    await current.start()
                stage_ready(settings, current)
                current = None
                return True
            except Exception as exc:
                generation.failed_startups.add(settings.name)
                server_warnings[settings.name] = f"{settings.name}: {type(exc).__name__}: {exc}"[
                    :2000
                ]
                generation.initial_failures[settings.name] = (
                    f"{settings.name}: {type(exc).__name__}: {exc}"
                )
                if settings.required:
                    required_failures[settings.name] = (
                        f"{settings.name}: {type(exc).__name__}: {exc}"
                    )
                if current is not None:
                    self._retired.add(current)
                    with suppress(Exception):
                        await current.aclose()
                current = None
                return False

            except BaseException as exc:
                if isinstance(exc, asyncio.CancelledError):
                    server_warnings[settings.name] = f"{settings.name}: MCP startup cancelled"
                    generation.initial_failures[settings.name] = server_warnings[settings.name]
                    if settings.required:
                        required_failures[settings.name] = server_warnings[settings.name]
                if current is not None:
                    self._retired.add(current)
                    with suppress(BaseException):
                        await current.aclose()
                raise

        generation = MCPPreparation(
            desired,
            catalog,
            force_reconnect,
            owned,
            staged,
            prepared,
            warnings,
            server_warnings,
            required_failures,
            denied,
            server_metadata=metadata,
            approval_configuration=self._approval_persistence.configuration
            if self._approval_persistence
            else None,
        )

        def observe(task):
            if not task.cancelled():
                task.exception()

        for settings in desired:
            task = asyncio.create_task(
                stage_server(settings), name=f"corki-mcp-start-{settings.name}"
            )
            task.add_done_callback(observe)
            generation.tasks[settings.name] = task
        return generation

    def _stage_ready(self, generation, settings, current):
        visible = tuple(d for d in current.definitions if current.tool_filter.allows(d["name"]))
        source = (
            next((s.source for s in generation.catalog.servers if s.name == settings.name), None)
            if generation.catalog is not None
            else None
        )
        candidates = self._tool_candidates(
            settings.name,
            visible,
            current,
            source,
            current.server_instructions,
            generation.warnings,
            generation,
        )
        generation.tool_identities[settings.name] = tuple(
            project_tool(settings.name, d).identity for d in visible
        )
        current.call_tools = raw_call_bindings(
            project_tool(settings.name, definition, current.server_instructions)
            for definition in visible
        )
        current.remote_tools = frozenset(
            name
            for name, tool in current.call_tools.items()
            if tool_is_model_visible(tool.definition)
        )
        current.approval_annotations = {
            name: MCPToolAnnotations.from_mapping(tool.definition.get("annotations"))
            for name, tool in current.call_tools.items()
        }
        generation.staged[settings.name] = current
        generation.prepared[settings.name] = candidates
        generation.server_warnings.pop(settings.name, None)
        generation.required_failures.pop(settings.name, None)
        generation.failed_startups.discard(settings.name)

    def _new_connection(
        self, settings, policy, environment, agent_plugin, cache, catalog_item_limit
    ):
        """Factory captures transport inputs, never a disposable generation."""
        client = (
            HttpMCPClient(settings, environment=environment, agent_plugin=agent_plugin)
            if settings.transport == "http" and (environment is not None or agent_plugin)
            else create_client(settings)
        )
        if (
            self._oauth_home is not None
            and environment is None
            and hasattr(client, "set_oauth_store")
        ):
            client.set_oauth_store(self._oauth_home, self._oauth_store_mode)
        elif (
            self._oauth_file_home is not None
            and environment is None
            and hasattr(client, "set_oauth_file_home")
        ):
            client.set_oauth_file_home(self._oauth_file_home)
        if hasattr(client, "set_elicitation_router"):
            client.set_elicitation_router(self.elicitations)
        return MCPConnection(
            client,
            self._connection_closed,
            metadata=policy,
            tool_filter=MCPToolFilter.from_settings(settings),
            settings=settings,
            environment=environment,
            agent_plugin=agent_plugin,
            cache_context=cache,
            catalog_item_limit=catalog_item_limit,
        )

    def _tool_candidates(
        self, server, definitions, connection, source, instructions, warnings, generation
    ):
        candidates = []
        for definition in definitions:
            try:
                candidates.append(
                    MCPTool(
                        server,
                        definition,
                        connection,
                        exposure=ToolExposure.DEFERRED
                        if self._defer_tools
                        else ToolExposure.DIRECT,
                        call_router=self.call_tool,
                        readiness=self.wait_until_ready,
                        server_instructions=instructions,
                        plugin_id=source.identity
                        if source is not None and source.kind in ("plugin", "selected_plugin")
                        else None,
                        plugin_display_names=source.plugin_display_names
                        if source is not None
                        else (),
                        agent_plugin=source.agent_plugin if source is not None else False,
                    )
                )
            except MCPInputSchemaError as error:
                if len(warnings) < 32:
                    warnings.append(
                        f"Skipping MCP tool {server}/{definition['name']}: {error}"[:2000]
                    )
        return tuple(candidates)

    def _publish_generation(self, generation: MCPPreparation, fallbacks: dict) -> None:
        count = generation.completed_count
        cached = {
            name: snapshot
            for name, cache in generation.catalog_contexts.items()
            if not generation.tasks[name].done()
            and name in generation.cache_eligible
            and (snapshot := cache.current_tools_or(fallbacks.get(name))) is not None
        }
        if (
            generation.published_count == count
            and generation.published_cached == cached
            and generation.published_epoch == generation.catalog_epoch
        ):
            return
        for task in generation.tasks.values():
            if task.done() and not task.cancelled():
                task.result()
        desired, catalog = generation.desired, generation.catalog
        staged, prepared = generation.staged, dict(generation.prepared)
        settings_by_name = {s.name: s for s in desired}
        sources = {s.name: s.source for s in catalog.servers} if catalog is not None else {}
        warnings = list(generation.warnings)
        identities = dict(generation.tool_identities)
        for name, snapshot in cached.items():
            connection = generation.pending_connections.get(name)
            tool_filter = MCPToolFilter.from_settings(settings_by_name[name])
            source = sources.get(name)
            definitions = deepcopy(
                tuple(
                    definition
                    for definition in snapshot.definitions
                    if tool_filter.allows(definition["name"])
                )
            )
            prepared[name] = self._tool_candidates(
                name,
                definitions,
                connection,
                source,
                snapshot.instructions,
                warnings,
                generation,
            )
            identities[name] = tuple(project_tool(name, d).identity for d in definitions)
        server_warnings = generation.server_warnings
        required_failures, denied = generation.required_failures, generation.denied
        # Completion order is not source priority. Publish the ready subset
        # in captured catalog order, retaining pending preparations separately.
        staged = {s.name: staged[s.name] for s in desired if s.name in staged}
        tools = [tool for s in desired for tool in prepared.get(s.name, ())]
        warnings.extend(server_warnings[s.name] for s in desired if s.name in server_warnings)
        raw_tools = {}
        for tool in tools:
            raw_tools.setdefault(tool.projection.identity.raw_identity, tool)
        names = normalize_tool_names(
            (identity for entries in identities.values() for identity in entries),
            prefix=self._prefix_tool_names,
            non_prefixed_servers=self._non_prefixed_servers,
        )
        tools = [
            raw_tools[name.raw_identity].with_model_name(
                name,
                omitted_surfaces=settings_by_name[name.server].omit_tools_from or (),
                exposure=self._registry.namespace_policy.mcp_exposure(
                    name.canonical,
                    settings_by_name[name.server].omit_tools_from,
                    search_enabled=self._defer_tools,
                    code_mode_only=self._code_mode_only,
                ),
            )
            for name in names
            if name.raw_identity in raw_tools
        ]
        # Resource entry points exist for enabled connection slots, including
        # pending/dormant/failed startup. Their Step binding owns actual readiness.
        if any(s.enabled and s.name not in denied for s in desired):
            tools.extend(self._aggregate_tools[:3])
        if staged:
            # Prompt helpers are Corki's extra API, not the native resource gate.
            tools.extend(self._aggregate_tools[3:])
        # App-only tools participate in global name normalization above, but
        # are absent from model discovery and callable bindings.
        tools = [tool for tool in tools if not isinstance(tool, MCPTool) or tool.model_visible]
        tools = apply_agent_budget(tools)
        # No suspension between registry and manager publication.
        # Validation/deepcopy in replace_owned finishes before it swaps.
        if self._closed:
            raise RuntimeError("MCP manager closed during refresh")
        self._registry.replace_owned(self._owner, tuple(tools))
        selected = self._registry.selected_owned_names(self._owner)
        tool_names = tuple(tool.spec.name for tool in tools if tool.spec.name in selected)
        warnings.extend(
            f"skipping MCP tool already registered or reserved: {tool.spec.name}"
            for tool in tools
            if tool.spec.name not in selected
        )
        previous = tuple(self._clients_by_name.values())
        self._clients_by_name = staged
        self._published_catalog = catalog
        self._denied_servers = denied
        self.tool_names = tool_names
        self.warnings = tuple(warnings[:32])
        self._required_startup_failures = tuple(
            required_failures[name] for name in sorted(required_failures)
        )
        self._started = True
        generation.published_count = count
        generation.published_epoch = generation.catalog_epoch
        generation.published_cached = cached
        retained = set(staged.values())
        bound = {
            connection
            for captured in self._bound_preparations.values()
            for connection in captured.owned
        }
        for connection in previous:
            if connection not in retained and connection not in bound:
                self._retired.add(connection)
                connection.retire()

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
            self.cancel_startup()
            self._shutdown_task = asyncio.create_task(self._shutdown(), name="mcp-shutdown")
        await asyncio.shield(self._shutdown_task)

    async def _shutdown(self) -> None:
        async with self._gate:
            await self._dispose_preparation()
            for generation in tuple(self._bound_preparations.values()):
                await asyncio.shield(self._close_preparation(generation))
            connections = (*self._clients_by_name.values(), *self._retired)
            for connection in connections:
                try:
                    await connection.aclose()
                except BaseException as exc:
                    self._close_error = self._close_error or exc
            self._clients_by_name.clear()
            self._retired.clear()
            await asyncio.gather(
                *(
                    asyncio.shield(binding.closed)
                    for binding in tuple(self._resource_bindings)
                    if binding.released
                ),
                return_exceptions=True,
            )
            if self._close_error is not None:
                raise self._close_error

    async def list_capability(
        self,
        kind: str,
        server: str | None,
        cursor: str | None = None,
        *,
        excluded_servers: frozenset[str] = frozenset(),
    ) -> dict[str, object]:
        ensure_model_access(server, excluded_servers)
        await self.refresh_if_dirty()
        methods = {
            "resources": "list_resources",
            "templates": "list_resource_templates",
            "prompts": "list_prompts",
        }
        if kind not in methods:
            raise ValueError(f"unknown MCP capability: {kind}")
        if cursor is not None and server is None:
            raise ValueError("cursor can only be used when a server is specified")
        if kind != "prompts" and server is not None:
            await self.prepare_server(server)
            page = await self._client(server).invoke(methods[kind], cursor)
            field = "resources" if kind == "resources" else "resourceTemplates"
            result = {"server": server, field: [{"server": server, **item} for item in page[field]]}
            if page.get("nextCursor") is not None:
                result["nextCursor"] = page["nextCursor"]
            return result
        clients = (
            ((server, self._client(server)),)
            if server is not None
            else tuple(self._clients_by_name.items())
        )
        output = {}
        with ExitStack() as leases:
            captured = tuple(
                (name, leases.enter_context(client.lease()))
                for name, client in clients
                if name not in excluded_servers
            )
            if kind != "prompts":
                return await aggregate_resources(captured, kind)
            for name, client in captured:
                output[name] = await getattr(client, methods[kind])()
        return output

    async def read_resource(self, server: str, uri: str):
        await self.prepare_server(server)
        return await self._client(server).invoke("read_resource", uri)

    async def get_prompt(self, server: str, name: str, arguments: dict[str, str]):
        await self.prepare_server(server)
        return await self._client(server).invoke("get_prompt", name, arguments)

    async def wait_until_ready(self, server):
        # Readiness never approves or invokes a call. Actual dispatch still
        # acquires current authority and produces its typed error Observation.
        async with self._selected_generation() as generation:
            with suppress(KeyError, MCPProtocolError):
                await self._wait_selected_client(generation, server)

    async def call_tool(
        self,
        server: str,
        name: str,
        arguments,
        *,
        on_external_context=None,
        on_output_token_limit=None,
        call_id=None,
    ):
        # Model schemas remain Step-owned; dispatch acquires a latest exact call.
        async with self.prepare_call(server, name) as prepared:
            result, _metadata = await prepared.call_with_metadata(
                arguments,
                on_external_context=on_external_context,
                on_output_token_limit=on_output_token_limit,
            )
            return result

    async def call_hook(self, server, name, arguments, *, timeout, thread_id):
        """Invoke host-trusted policy on a ready connection, without model dispatch."""
        from corki.mcp.client import validate_tool_result
        from corki.mcp.request_policy import request_timeout

        await self.publish_pending()
        async with self._gate:
            if self._closed:
                raise MCPUnavailableError("MCP manager is closed")
            connection = self._clients_by_name.get(server)
            if connection is None or connection.client.is_closed:
                raise MCPUnavailableError(f"MCP hook server is not ready: {server}")
            if not connection.tool_filter.allows(name):
                raise MCPDisabledError(f"tool '{name}' is disabled for MCP server '{server}'")
            if name not in connection.remote_tools:
                raise MCPUnavailableError(f"unknown MCP tool: {server}/{name}")
            lease = connection.lease()
            client = lease.__enter__()
        try:
            with request_timeout(client, timeout):
                result = validate_tool_result(
                    await client.request(
                        "tools/call",
                        {
                            "name": name,
                            "arguments": arguments,
                            "_meta": {"threadId": str(thread_id)},
                        },
                    )
                )
            text = "\n".join(
                item["text"] for item in result["content"] if item.get("type") == "text"
            )
            if result.get("isError"):
                raise MCPProtocolError(f"MCP tool returned an error: {text}")
            if len(text.encode("utf-8")) > 1024 * 1024:
                raise ValueError("MCP hook output exceeds 1 MiB")
            return text
        finally:
            lease.__exit__(None, None, None)

    @asynccontextmanager
    async def prepare_call(self, server: str, name: str):
        """Borrow exact call authority; later catalog replacement may make it stale."""
        async with self._selected_generation() as generation:
            await self._wait_selected_client(generation, server)
            async with generation.catalog_revision.read(), self._gate:
                if self._closed:
                    raise RuntimeError("MCP manager is closed")
                source = generation.staged[server]
                if generation is self._preparation and not self._pending:
                    self._publish_generation(generation, {})
                if not source.tool_filter.allows(name):
                    raise MCPDisabledError(f"tool '{name}' is disabled for MCP server '{server}'")
                if name not in source.remote_tools:
                    raise MCPUnavailableError(f"unknown MCP tool: {server}/{name}")
                connection = source.fork(source.settings, source.metadata)
                self._retired.add(connection)
                annotations = source.approval_annotations[name]
                declaring = (
                    next(
                        (
                            entry.source
                            for entry in generation.catalog.servers
                            if entry.name == server
                        ),
                        None,
                    )
                    if generation.catalog is not None
                    else None
                )
                allow_persistent = self._tool_call_elicitation and (
                    declaring is None or declaring.kind != "selected_plugin"
                )

                async def approve(arguments, metadata, approval_mode):
                    return await self._approvals.check(
                        connection.settings,
                        name,
                        arguments,
                        annotations,
                        allow_persistent=allow_persistent,
                    )

                async def persist(key):
                    if self._approval_persistence is not None:
                        await self._approval_persistence.persist(
                            key, generation.approval_configuration, declaring
                        )

                async def apply(decision):
                    if decision is not None and decision.persistent:
                        await self._approvals.apply(decision, persist=persist)
                    else:
                        await self._approvals.apply(decision)

                prepared = MCPPreparedCall(
                    connection,
                    name,
                    generation.catalog_revision,
                    approve,
                    apply_approval=apply,
                    tool_info=source.call_tools[name],
                    approval_policy=self._approval_policy,
                )
            try:
                yield prepared
            finally:
                connection.retire()

    def _client(self, server: str) -> MCPConnection:
        if server in self._denied_servers:
            raise MCPProtocolError(
                f"MCP server '{server}' is disabled by {self._denied_servers[server]}"
            )
        client = self._clients_by_name.get(server)
        if client is None:
            raise KeyError(f"unknown MCP server: {server}")
        return client
