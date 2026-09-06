"""Single source of truth for model schemas and executable tool handlers."""

from __future__ import annotations

from copy import deepcopy

from corki.protocol.tools import ToolSpec
from corki.tools.base import Tool


class DuplicateToolError(ValueError):
    pass


class ToolRegistry:
    """Mutable during composition and read-only while turns are executing."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._sealed = False
        self._specs: dict[str, ToolSpec] = {}

    def register(self, tool: Tool) -> None:
        if self._sealed:
            raise RuntimeError("tool registry is sealed for runtime execution")
        name = tool.spec.name
        if name in self._tools:
            raise DuplicateToolError(f"tool already registered: {name}")
        self._tools[name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def unregister(self, name: str) -> None:
        """Remove a tool during composition rollback; running turns never call this."""

        if self._sealed:
            raise RuntimeError("tool registry is sealed for runtime execution")
        self._tools.pop(name, None)

    def seal(self) -> None:
        """Prevent schema/handler drift after the runtime starts serving turns."""

        if not self._sealed:
            self._specs = {name: deepcopy(tool.spec) for name, tool in self._tools.items()}
            self._sealed = True

    def specs(self) -> tuple[ToolSpec, ...]:
        values = (
            self._specs.values() if self._sealed else (tool.spec for tool in self._tools.values())
        )
        return tuple(deepcopy(spec) for spec in values)

    def spec(self, name: str) -> ToolSpec | None:
        """Return an isolated definition from the current registration generation."""

        if self._sealed:
            return deepcopy(self._specs.get(name))
        tool = self._tools.get(name)
        return deepcopy(tool.spec) if tool is not None else None

    def model_visible_specs(self) -> tuple[ToolSpec, ...]:
        """Freeze the directly advertised tool plan for one model step."""

        return tuple(spec for spec in self.specs() if spec.exposure.is_model_visible)
