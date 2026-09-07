"""Single source of truth for model schemas and executable tool handlers."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from corki.protocol.tools import ToolSpec
from corki.tools.base import Tool


class DuplicateToolError(ValueError):
    pass


@dataclass(frozen=True)
class _PublishedRegistry:
    tools: dict[str, Tool]
    specs: dict[str, ToolSpec]


@dataclass(frozen=True)
class ToolRegistrySnapshot:
    """Read-only generation owning exact handlers and isolated exported definitions."""

    _published: _PublishedRegistry

    def get(self, name: str) -> Tool | None:
        return self._published.tools.get(name)

    def spec(self, name: str) -> ToolSpec | None:
        return deepcopy(self._published.specs.get(name))

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(deepcopy(spec) for spec in self._published.specs.values())

    def model_visible_specs(self) -> tuple[ToolSpec, ...]:
        return tuple(spec for spec in self.specs() if spec.exposure.is_model_visible)


class ToolRegistry:
    """Sealed ordinary registration, with atomic owner-scoped runtime publication."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._sealed = False
        self._published: _PublishedRegistry | None = None
        self._owners: dict[object, set[str]] = {}

    def register(self, tool: Tool) -> None:
        if self._sealed:
            raise RuntimeError("tool registry is sealed for runtime execution")
        name = tool.spec.name
        if name in self._tools:
            raise DuplicateToolError(f"tool already registered: {name}")
        self._tools[name] = tool

    def get(self, name: str) -> Tool | None:
        tools = self._published.tools if self._published is not None else self._tools
        return tools.get(name)

    def snapshot(self) -> ToolRegistrySnapshot:
        """Capture one generation without awaiting; published mappings are never mutated."""
        published = self._published
        if published is None:
            published = _PublishedRegistry(
                dict(self._tools), {name: deepcopy(tool.spec) for name, tool in self._tools.items()}
            )
        return ToolRegistrySnapshot(published)

    def create_owner(self) -> object:
        """Reserve a publication capability during composition, before sealing."""
        if self._sealed:
            raise RuntimeError("tool registry is sealed for runtime execution")
        owner = object()
        self._owners[owner] = set()
        return owner

    def owned_names(self, owner: object) -> frozenset[str]:
        return frozenset(self._owners[owner])

    def replace_owned(self, owner: object, tools: tuple[Tool, ...]) -> None:
        """Publish handlers and isolated definitions together; failed builds change nothing.

        Called on the owning event loop, with no suspension during publication.
        A capability cannot replace another owner's or ordinary registered tools.
        """
        previous_names = self._owners[owner]
        current = self._published.tools if self._published is not None else self._tools
        replacements = {}
        specs = {}
        for tool in tools:
            spec = deepcopy(tool.spec)
            name = spec.name
            if name in replacements or (name in current and name not in previous_names):
                raise DuplicateToolError(f"tool already registered: {name}")
            replacements[name], specs[name] = tool, spec
        next_tools = {name: tool for name, tool in current.items() if name not in previous_names}
        next_tools.update(replacements)
        if self._published is not None:
            next_specs = {
                name: spec
                for name, spec in self._published.specs.items()
                if name not in previous_names
            }
            next_specs.update(specs)
            self._published = _PublishedRegistry(next_tools, next_specs)
        else:
            self._tools = next_tools
        self._owners[owner] = set(replacements)

    def unregister(self, name: str) -> None:
        """Remove a tool during composition rollback; running turns never call this."""

        if self._sealed:
            raise RuntimeError("tool registry is sealed for runtime execution")
        self._tools.pop(name, None)
        for names in self._owners.values():
            names.discard(name)

    def seal(self) -> None:
        """Prevent schema/handler drift after the runtime starts serving turns."""

        if not self._sealed:
            specs = {name: deepcopy(tool.spec) for name, tool in self._tools.items()}
            self._published = _PublishedRegistry(dict(self._tools), specs)
            self._tools = {}  # Do not retain superseded handlers through the composition map.
            self._sealed = True

    def specs(self) -> tuple[ToolSpec, ...]:
        values = (
            self._published.specs.values()
            if self._published is not None
            else (tool.spec for tool in self._tools.values())
        )
        return tuple(deepcopy(spec) for spec in values)

    def deferred_entries(self) -> tuple[tuple[str, Tool], ...]:
        """Capture ordered deferred handlers without copying immutable search schemas.

        The owning event loop consumes this snapshot without suspension. Published
        exposure comes from the registration, not a subsequently mutated handler.
        """
        published = self._published
        if published is not None:
            return tuple(
                (name, published.tools[name])
                for name, spec in published.specs.items()
                if spec.exposure.is_deferred
            )
        return tuple(
            (name, tool) for name, tool in self._tools.items() if tool.spec.exposure.is_deferred
        )

    def spec(self, name: str) -> ToolSpec | None:
        """Return an isolated definition from the current registration generation."""

        if self._published is not None:
            return deepcopy(self._published.specs.get(name))
        tool = self._tools.get(name)
        return deepcopy(tool.spec) if tool is not None else None

    def model_visible_specs(self) -> tuple[ToolSpec, ...]:
        """Freeze the directly advertised tool plan for one model step."""

        return tuple(spec for spec in self.specs() if spec.exposure.is_model_visible)
