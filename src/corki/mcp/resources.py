"""Aggregate MCP resources and prompts across connected servers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from corki.config.mcp_headers import RUST_WHITESPACE
from corki.context.hosted_output import truncate_output_text
from corki.protocol.tools import ToolCall, ToolConcurrency, ToolResult, ToolSpec
from corki.protocol.wire_json import loads_wire, materialize
from corki.protocol.wire_numbers import dumps_wire
from corki.tools import ToolContext

if TYPE_CHECKING:
    from corki.mcp.manager import MCPManager


def _resource_arguments(call: ToolCall, *, read: bool = False) -> dict:
    fields = ("server", "uri") if read else ("server", "cursor")
    raw = call.raw_arguments
    if call.parse_error is not None and not raw:
        raise ValueError(f"invalid JSON arguments: {call.parse_error}")
    value = (
        materialize(loads_wire(raw), preserve_pairs=False)
        if raw and raw.strip(RUST_WHITESPACE)
        else None
        if raw
        else call.arguments
    )
    if isinstance(value, list):
        if len(value) > 2 or (read and len(value) != 2):
            raise ValueError("invalid resource argument sequence")
        value = dict(zip(fields, value, strict=False))
    if value is None and not read:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("resource arguments must be an object")
    result = {}
    for field in fields:
        item = value.get(field)
        if item is None and not read:
            continue
        if not isinstance(item, str):
            raise ValueError(f"{field} must be a string")
        item = item.strip(RUST_WHITESPACE)
        if not item and read:
            raise ValueError(f"{field} must be provided")
        if item:
            result[field] = item
    if not read and "cursor" in result and "server" not in result:
        raise ValueError("cursor can only be used when a server is specified")
    return result


def _resource_output(call: ToolCall, payload: dict, context: ToolContext) -> ToolResult:
    # The native resource handler bounds the serialized payload before archival.
    content = truncate_output_text(
        dumps_wire(payload), context.model_output_policy.history_allowance()
    )
    return ToolResult(call.id, call.name, content)


class MCPResourceInventoryTool:
    """Named single pages or a failure-isolated aggregate of resource catalogs."""

    def __init__(self, manager: MCPManager, kind: str) -> None:
        self._manager, self._kind = manager, kind

    @property
    def spec(self) -> ToolSpec:
        name = "list_mcp_resources" if self._kind == "resources" else "list_mcp_resource_templates"
        return ToolSpec(
            name,
            f"List MCP {self._kind}. Specify a server for one page; omit it to list all servers.",
            {
                "type": "object",
                "properties": {"server": {"type": "string"}, "cursor": {"type": "string"}},
                "additionalProperties": False,
            },
            concurrency=ToolConcurrency.PARALLEL,
        )

    def parse_call_arguments(self, call: ToolCall) -> dict:
        return _resource_arguments(call)

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        args = _resource_arguments(call)
        resources = context.mcp_resources or self._manager
        payload = await resources.list_capability(
            self._kind,
            args.get("server"),
            args.get("cursor"),
        )
        return _resource_output(call, payload, context)


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
        )

    def __init__(self, manager: MCPManager) -> None:
        self._manager = manager

    def parse_call_arguments(self, call: ToolCall) -> dict:
        return _resource_arguments(call, read=True)

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        args = _resource_arguments(call, read=True)
        resources = context.mcp_resources or self._manager
        value = await resources.read_resource(args["server"], args["uri"])
        return _resource_output(
            call, {"server": args["server"], "uri": args["uri"], **value}, context
        )


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
        MCPResourceInventoryTool(manager, "resources"),
        MCPResourceInventoryTool(manager, "templates"),
        MCPReadResourceTool(manager),
        MCPInventoryTool(manager, "prompts"),
        MCPGetPromptTool(manager),
    )
