"""Aggregate MCP resources and prompts across connected servers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from corki.protocol.tools import ToolCall, ToolConcurrency, ToolResult, ToolSpec
from corki.tools import ToolContext

if TYPE_CHECKING:
    from corki.mcp.manager import MCPManager


class MCPInventoryTool:
    """List resources, templates, or prompts with explicit server provenance."""

    def __init__(self, manager: MCPManager, kind: str) -> None:
        self._manager = manager
        self._kind = kind

    @property
    def spec(self) -> ToolSpec:
        names = {
            "resources": "list_mcp_resources",
            "templates": "list_mcp_resource_templates",
            "prompts": "list_mcp_prompts",
        }
        return ToolSpec(
            names[self._kind],
            f"List available MCP {self._kind}, including their source server.",
            {
                "type": "object",
                "properties": {"server": {"type": "string"}},
                "additionalProperties": False,
            },
            concurrency=ToolConcurrency.PARALLEL,
            output_char_budget=160_000,
        )

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        assert call.arguments is not None
        requested = call.arguments.get("server")
        server = str(requested) if requested is not None else None
        try:
            payload = await self._manager.list_capability(self._kind, server)
        except (KeyError, ValueError) as exc:
            return ToolResult(call.id, call.name, str(exc), is_error=True)
        return ToolResult(call.id, call.name, json.dumps(payload, ensure_ascii=False))


class MCPReadResourceTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            "read_mcp_resource",
            "Read an exact resource URI from a connected MCP server.",
            {
                "type": "object",
                "properties": {
                    "server": {"type": "string"},
                    "uri": {"type": "string"},
                },
                "required": ["server", "uri"],
                "additionalProperties": False,
            },
            concurrency=ToolConcurrency.PARALLEL,
            output_char_budget=160_000,
        )

    def __init__(self, manager: MCPManager) -> None:
        self._manager = manager

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        assert call.arguments is not None
        try:
            value = await self._manager.read_resource(
                str(call.arguments["server"]), str(call.arguments["uri"])
            )
        except KeyError as exc:
            return ToolResult(call.id, call.name, str(exc), is_error=True)
        return ToolResult(call.id, call.name, json.dumps(value, ensure_ascii=False))


class MCPGetPromptTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            "get_mcp_prompt",
            "Render a named prompt supplied by a connected MCP server.",
            {
                "type": "object",
                "properties": {
                    "server": {"type": "string"},
                    "name": {"type": "string"},
                    "arguments": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["server", "name"],
                "additionalProperties": False,
            },
            concurrency=ToolConcurrency.PARALLEL,
            output_char_budget=160_000,
        )

    def __init__(self, manager: MCPManager) -> None:
        self._manager = manager

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        del context
        assert call.arguments is not None
        raw_arguments = call.arguments.get("arguments", {})
        if not isinstance(raw_arguments, dict):
            return ToolResult(call.id, call.name, "arguments must be an object", is_error=True)
        arguments = {str(key): str(value) for key, value in raw_arguments.items()}
        try:
            value = await self._manager.get_prompt(
                str(call.arguments["server"]),
                str(call.arguments["name"]),
                arguments,
            )
        except KeyError as exc:
            return ToolResult(call.id, call.name, str(exc), is_error=True)
        return ToolResult(call.id, call.name, json.dumps(value, ensure_ascii=False))


def resource_tools(manager: MCPManager):
    return (
        MCPInventoryTool(manager, "resources"),
        MCPInventoryTool(manager, "templates"),
        MCPReadResourceTool(manager),
        MCPInventoryTool(manager, "prompts"),
        MCPGetPromptTool(manager),
    )
