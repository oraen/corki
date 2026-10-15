"""Single source of truth for model schemas and executable tool handlers."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from enum import IntEnum

from corki.protocol.tool_exposure import ToolNamespacePolicy
from corki.protocol.tools import ToolSpec
from corki.tools.base import Tool


class DuplicateToolError(ValueError):
    pass


class ToolSource(IntEnum):
    """Host-selected external registration order, never inferred from tool metadata."""

    MCP = 1
    EXTENSION = 2
    DYNAMIC = 3


@dataclass(frozen=True)
class _PublishedRegistry:
    tools: dict[str, Tool]
    specs: dict[str, ToolSpec]
    mcp_servers: dict[str, str | None]
    mcp_masks: dict[str, tuple[str, ...] | None]
    mcp_plugins: dict[str, str | None]


def _mcp_server(tool: Tool) -> str | None:
    # This is a trusted handler capability, not ToolSpec.source or remote _meta.
    value = getattr(tool, "mcp_server_name", None)
    if value is not None and not isinstance(value, str):
        raise ValueError("MCP dispatcher owner must be a server name string")
    return value


def _mcp_mask(tool: Tool) -> tuple[str, ...] | None:
    value = getattr(tool, "mcp_omit_tools_from", None)
    if value is not None and (
        not isinstance(value, tuple)
        or any(
            not isinstance(s, str) or s not in {"direct", "deferred", "code_mode"} for s in value
        )
    ):
        raise ValueError("MCP exposure mask must be a host-owned tuple of surfaces")
    return value


def _mcp_plugin(tool: Tool) -> str | None:
    value = getattr(tool, "mcp_plugin_id", None)
    if value is not None and (not isinstance(value, str) or not value or _mcp_server(tool) is None):
        raise ValueError("MCP plugin attribution requires a host identity and server binding")
    return value


@dataclass(frozen=True)
class _ExternalEntry:
    tool: Tool
    spec: ToolSpec
    server: str | None
    mask: tuple[str, ...] | None
    plugin: str | None

    @classmethod
    def capture(cls, tool):
        return cls(tool, deepcopy(tool.spec), _mcp_server(tool), _mcp_mask(tool), _mcp_plugin(tool))


@dataclass(frozen=True)
class _RegistrySources:
    trusted: _PublishedRegistry
    owners: dict[str, object]
    external: tuple[tuple[ToolSource, object, tuple[_ExternalEntry, ...]], ...]

    def resolve(self, policy, excluding=frozenset()):
        names = {n for n in self.trusted.tools if self.owners.get(n) not in excluding}
        handlers = {n: t for n, t in self.trusted.tools.items() if n in names}
        specs = {n: s for n, s in self.trusted.specs.items() if n in names}
        servers = {n: s for n, s in self.trusted.mcp_servers.items() if n in names}
        masks = {n: s for n, s in self.trusted.mcp_masks.items() if n in names}
        plugins = {n: s for n, s in self.trusted.mcp_plugins.items() if n in names}
        owners = {n: o for n, o in self.owners.items() if n in names}
        collision = None
        for _source, owner, entries in self.external:
            if owner in excluding:
                continue
            for entry in entries:
                name = entry.spec.name
                if name in handlers:
                    if collision is None:
                        collision = name
                    continue
                if name in {"exec_command", "shell_command"}:
                    # External tools cannot capture terminal controls even if
                    # this Turn does not register a trusted terminal handler.
                    continue
                handlers[name], specs[name] = entry.tool, entry.spec
                servers[name], masks[name] = entry.server, entry.mask
                plugins[name] = entry.plugin
                owners[name] = owner
        return ToolRegistrySnapshot(
            _PublishedRegistry(handlers, specs, servers, masks, plugins),
            policy,
            first_collision=collision,
            _source_catalog=self,
            _selected_owners=owners,
        )


@dataclass(frozen=True)
class ToolRegistrySnapshot:
    """Read-only generation owning exact handlers and isolated exported definitions."""

    _published: _PublishedRegistry
    namespace_policy: ToolNamespacePolicy = ToolNamespacePolicy()
    tool_mode: str | None = None
    router_source: ToolRegistrySnapshot | None = field(default=None, repr=False, compare=False)
    search_enabled: bool | None = None
    first_collision: str | None = None
    _source_catalog: _RegistrySources | None = field(default=None, repr=False, compare=False)
    _selected_owners: dict[str, object] = field(default_factory=dict, repr=False, compare=False)

    def without_owners(self, owners: frozenset[object]) -> ToolRegistrySnapshot:
        """Re-resolve captured sources, including candidates shadowed by controls."""
        if self._source_catalog is None:
            return self.derive(
                remove=frozenset(n for n, o in self._selected_owners.items() if o in owners)
            )
        return self._source_catalog.resolve(self.namespace_policy, excluding=owners)

    def owned_names(self, owner: object) -> frozenset[str]:
        return frozenset(n for n, o in self._selected_owners.items() if o is owner)

    def get(self, name: str) -> Tool | None:
        return self._published.tools.get(name)

    def spec(self, name: str) -> ToolSpec | None:
        spec = self._published.specs.get(name)
        return deepcopy(self.namespace_policy.project(spec)) if spec is not None else None

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(
            deepcopy(self.namespace_policy.project(spec)) for spec in self._published.specs.values()
        )

    def model_visible_specs(self) -> tuple[ToolSpec, ...]:
        return tuple(spec for spec in self.specs() if spec.exposure.is_model_visible)

    def mcp_server_name(self, name: str) -> str | None:
        return self._published.mcp_servers.get(name)

    def mcp_mask(self, name: str) -> tuple[str, ...] | None:
        return self._published.mcp_masks.get(name)

    def mcp_plugin_id(self, name: str) -> str | None:
        """Plugin owning the captured winning handler, not a same-name declaration."""
        return self._published.mcp_plugins.get(name)

    def deferred_entries(self) -> tuple[tuple[str, Tool], ...]:
        return tuple(
            (name, self._published.tools[name])
            for name, spec in self._published.specs.items()
            if self.namespace_policy.project(spec).exposure.is_deferred
        )

    def derive(
        self,
        *,
        specs: tuple[ToolSpec, ...] = (),
        tools: tuple[Tool, ...] = (),
        remove: frozenset[str] = frozenset(),
        tool_mode: str | None = None,
        search_enabled: bool | None = None,
    ) -> ToolRegistrySnapshot:
        """Build an isolated host candidate; never publish it to the source registry."""
        handlers = {n: t for n, t in self._published.tools.items() if n not in remove}
        definitions = {n: s for n, s in self._published.specs.items() if n not in remove}
        servers = {n: s for n, s in self._published.mcp_servers.items() if n not in remove}
        masks = {n: s for n, s in self._published.mcp_masks.items() if n not in remove}
        plugins = {n: s for n, s in self._published.mcp_plugins.items() if n not in remove}
        for spec in specs:
            if spec.name not in handlers:
                raise ValueError("candidate spec has no registered handler: " + spec.name)
            definitions[spec.name] = deepcopy(spec)
        for tool in tools:
            spec = deepcopy(tool.spec)
            handlers[spec.name], definitions[spec.name] = tool, spec
            servers[spec.name], masks[spec.name] = _mcp_server(tool), _mcp_mask(tool)
            plugins[spec.name] = _mcp_plugin(tool)
        return ToolRegistrySnapshot(
            _PublishedRegistry(handlers, definitions, servers, masks, plugins),
            self.namespace_policy,
            self.tool_mode if tool_mode is None else tool_mode,
            self.router_source,
            self.search_enabled if search_enabled is None else search_enabled,
            self.first_collision,
            _selected_owners={n: o for n, o in self._selected_owners.items() if n not in remove},
        )


class ToolRegistry:
    """Sealed ordinary registration, with atomic owner-scoped runtime publication."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._sealed = False
        self._published: _PublishedRegistry | None = None
        self._owners: dict[object, set[str]] = {}
        self._external: dict[object, tuple[ToolSource, tuple[_ExternalEntry, ...]]] = {}
        self._cached_snapshot: ToolRegistrySnapshot | None = None
        self._namespace_policy = ToolNamespacePolicy()

    @property
    def namespace_policy(self) -> ToolNamespacePolicy:
        return self._namespace_policy

    @contextmanager
    def composition(self):
        """Roll back failed synchronous composition before yielding to cleanup.

        Like publication, this scope must not suspend or race another thread.
        Handler objects are retained by identity, not cloned or reset internally.
        """
        previous = (
            dict(self._tools),
            self._sealed,
            self._published,
            {owner: set(names) for owner, names in self._owners.items()},
            dict(self._external),
            self._cached_snapshot,
            self._namespace_policy,
        )
        try:
            yield
        except BaseException:
            (
                self._tools,
                self._sealed,
                self._published,
                self._owners,
                self._external,
                self._cached_snapshot,
                self._namespace_policy,
            ) = previous
            raise

    def configure_namespace_policy(self, policy: ToolNamespacePolicy) -> None:
        """Bind host policy at composition; published Step views remain immutable."""
        if self._sealed:
            raise RuntimeError("tool registry is sealed for runtime execution")
        if not isinstance(policy, ToolNamespacePolicy):
            raise ValueError("namespace policy must be host-owned")
        self._namespace_policy = policy
        self._cached_snapshot = None

    def register(self, tool: Tool) -> None:
        if self._sealed:
            raise RuntimeError("tool registry is sealed for runtime execution")
        name = tool.spec.name
        if name in self._tools:
            raise DuplicateToolError(f"tool already registered: {name}")
        self._tools[name] = tool
        self._cached_snapshot = None

    def get(self, name: str) -> Tool | None:
        if self._external:
            return self.snapshot().get(name)
        tools = self._published.tools if self._published is not None else self._tools
        return tools.get(name)

    def snapshot(self) -> ToolRegistrySnapshot:
        """Capture one generation without awaiting; published mappings are never mutated."""
        if self._sealed and self._cached_snapshot is not None:
            return self._cached_snapshot
        published = self._published
        if published is None:
            published = _PublishedRegistry(
                dict(self._tools),
                {name: deepcopy(tool.spec) for name, tool in self._tools.items()},
                {name: _mcp_server(tool) for name, tool in self._tools.items()},
                {name: _mcp_mask(tool) for name, tool in self._tools.items()},
                {name: _mcp_plugin(tool) for name, tool in self._tools.items()},
            )
        sources = _RegistrySources(
            published,
            {n: o for o, names in self._owners.items() if o not in self._external for n in names},
            tuple(
                sorted(
                    ((s, o, entries) for o, (s, entries) in self._external.items()),
                    key=lambda row: row[0],
                )
            ),
        )
        snapshot = sources.resolve(self.namespace_policy)
        if self._sealed:
            self._cached_snapshot = snapshot
        return snapshot

    def create_owner(self, *, source: ToolSource | None = None) -> object:
        """Reserve a publication capability during composition, before sealing."""
        if self._sealed:
            raise RuntimeError("tool registry is sealed for runtime execution")
        if source is not None and not isinstance(source, ToolSource):
            raise ValueError("external tool source must be a host-selected ToolSource")
        owner = object()
        self._owners[owner] = set()
        if source is not None:
            self._external[owner] = (source, ())
        self._cached_snapshot = None
        return owner

    def owned_names(self, owner: object) -> frozenset[str]:
        return frozenset(self._owners[owner])

    def selected_owned_names(self, owner: object) -> frozenset[str]:
        return self.snapshot().owned_names(owner)

    def append_external(self, owner: object, tool: Tool) -> None:
        """Append to an explicitly selected source without changing other owners."""
        source, entries = self._external[owner]
        entry = _ExternalEntry.capture(tool)
        self._external[owner] = (source, (*entries, entry))
        self._owners[owner].add(entry.spec.name)
        self._cached_snapshot = None

    def replace_owned(self, owner: object, tools: tuple[Tool, ...]) -> None:
        """Publish handlers and isolated definitions together; failed builds change nothing.

        Called on the owning event loop, with no suspension during publication.
        A capability cannot replace another owner's or ordinary registered tools.
        External capabilities replace only their candidate catalog; source order
        chooses winners without mutating trusted or other external catalogs.
        """
        if owner in self._external:
            self.prepare_external(owner, tools)()
            return
        previous_names = self._owners[owner]
        current = self._published.tools if self._published is not None else self._tools
        replacements = {}
        specs = {}
        servers = {}
        masks = {}
        plugins = {}
        for tool in tools:
            spec = deepcopy(tool.spec)
            name = spec.name
            if name in replacements or (name in current and name not in previous_names):
                raise DuplicateToolError(f"tool already registered: {name}")
            replacements[name], specs[name] = tool, spec
            servers[name] = _mcp_server(tool)
            masks[name] = _mcp_mask(tool)
            plugins[name] = _mcp_plugin(tool)
        next_tools = {name: tool for name, tool in current.items() if name not in previous_names}
        next_tools.update(replacements)
        if self._published is not None:
            next_specs = {
                name: spec
                for name, spec in self._published.specs.items()
                if name not in previous_names
            }
            next_specs.update(specs)
            next_servers = {
                name: server
                for name, server in self._published.mcp_servers.items()
                if name not in previous_names
            }
            next_servers.update(servers)
            next_masks = {
                name: mask
                for name, mask in self._published.mcp_masks.items()
                if name not in previous_names
            }
            next_masks.update(masks)
            next_plugins = {
                name: plugin
                for name, plugin in self._published.mcp_plugins.items()
                if name not in previous_names
            }
            next_plugins.update(plugins)
            self._published = _PublishedRegistry(
                next_tools, next_specs, next_servers, next_masks, next_plugins
            )
        else:
            self._tools = next_tools
        self._owners[owner] = set(replacements)
        self._cached_snapshot = None

    def prepare_external(self, owner: object, tools: tuple[Tool, ...]):
        """Validate an external generation before a multi-consumer synchronous commit."""
        source, _ = self._external[owner]
        entries = tuple(_ExternalEntry.capture(tool) for tool in tools)
        names = {entry.spec.name for entry in entries}

        def publish():
            self._external[owner] = (source, entries)
            self._owners[owner] = names
            self._cached_snapshot = None

        return publish

    def unregister(self, name: str) -> None:
        """Remove a tool during composition rollback; running turns never call this."""

        if self._sealed:
            raise RuntimeError("tool registry is sealed for runtime execution")
        self._tools.pop(name, None)
        for owner, names in self._owners.items():
            if owner not in self._external:
                names.discard(name)
        self._cached_snapshot = None

    def seal(self) -> None:
        """Prevent schema/handler drift after the runtime starts serving turns."""

        if not self._sealed:
            specs = {name: deepcopy(tool.spec) for name, tool in self._tools.items()}
            servers = {name: _mcp_server(tool) for name, tool in self._tools.items()}
            masks = {name: _mcp_mask(tool) for name, tool in self._tools.items()}
            plugins = {name: _mcp_plugin(tool) for name, tool in self._tools.items()}
            self._published = _PublishedRegistry(dict(self._tools), specs, servers, masks, plugins)
            self._tools = {}  # Do not retain superseded handlers through the composition map.
            self._sealed = True
            self._cached_snapshot = None

    def specs(self) -> tuple[ToolSpec, ...]:
        if self._external:
            return self.snapshot().specs()
        values = (
            self._published.specs.values()
            if self._published is not None
            else (tool.spec for tool in self._tools.values())
        )
        return tuple(deepcopy(self.namespace_policy.project(spec)) for spec in values)

    def deferred_entries(self) -> tuple[tuple[str, Tool], ...]:
        """Capture ordered deferred handlers without copying immutable search schemas.

        The owning event loop consumes this snapshot without suspension. Published
        exposure comes from the registration, not a subsequently mutated handler.
        """
        if self._external:
            return self.snapshot().deferred_entries()
        published = self._published
        if published is not None:
            return tuple(
                (name, published.tools[name])
                for name, spec in published.specs.items()
                if self.namespace_policy.project(spec).exposure.is_deferred
            )
        return tuple(
            (name, tool)
            for name, tool in self._tools.items()
            if self.namespace_policy.project(tool.spec).exposure.is_deferred
        )

    def spec(self, name: str) -> ToolSpec | None:
        """Return an isolated definition from the current registration generation."""

        if self._external:
            return self.snapshot().spec(name)
        if self._published is not None:
            spec = self._published.specs.get(name)
        else:
            tool = self._tools.get(name)
            spec = tool.spec if tool is not None else None
        return deepcopy(self.namespace_policy.project(spec)) if spec is not None else None

    def model_visible_specs(self) -> tuple[ToolSpec, ...]:
        """Freeze the directly advertised tool plan for one model step."""

        return tuple(spec for spec in self.specs() if spec.exposure.is_model_visible)
