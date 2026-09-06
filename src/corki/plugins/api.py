"""Narrow registration API exposed to trusted local Python plugins."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from corki.protocol.tools import ToolCall, ToolConcurrency, ToolExposure, ToolResult, ToolSpec
from corki.tools import ToolContext, ToolRegistry

PluginHandler = Callable[[Mapping[str, Any], ToolContext], object | Awaitable[object]]


@dataclass(slots=True)
class _PluginTool:
    _spec: ToolSpec
    _handler: PluginHandler

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        assert call.arguments is not None
        value = self._handler(call.arguments, context)
        if inspect.isawaitable(value):
            value = await value
        if isinstance(value, ToolResult):
            return ToolResult(
                call.id,
                call.name,
                value.content,
                is_error=value.is_error,
                display_content=value.display_content,
                attachments=value.attachments,
                state_update=value.state_update,
            )
        content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return ToolResult(call.id, call.name, content)


class PluginRegistrar:
    """Allow a plugin to register namespaced tools without core imports."""

    def __init__(self, namespace: str, registry: ToolRegistry) -> None:
        self.namespace = namespace
        self._registry = registry
        self._registered: list[str] = []

    @property
    def registered_tools(self) -> tuple[str, ...]:
        return tuple(self._registered)

    def register_tool(
        self,
        *,
        name: str,
        description: str,
        parameters: Mapping[str, Any],
        handler: PluginHandler,
        parallel: bool = False,
        output_char_budget: int | None = None,
        exposure: ToolExposure = ToolExposure.DIRECT,
    ) -> str:
        if not name or len(name) > 64 or not name.replace("_", "").isalnum():
            raise ValueError("plugin tool name must contain only letters, digits, and underscores")
        if not description or len(description) > 1_024:
            raise ValueError("plugin tool description must contain 1 to 1024 characters")
        exposed_name = f"plugin__{self.namespace}__{name}"
        if len(exposed_name) > 64:
            exposed_name = f"{exposed_name[:47]}_{sha256(exposed_name.encode()).hexdigest()[:16]}"
        spec = ToolSpec(
            exposed_name,
            description,
            parameters,
            exposure=ToolExposure(exposure),
            source=self.namespace,
            concurrency=ToolConcurrency.PARALLEL if parallel else ToolConcurrency.EXCLUSIVE,
            output_char_budget=output_char_budget,
        )
        self._registry.register(_PluginTool(spec, handler))
        self._registered.append(exposed_name)
        return exposed_name
