"""Adapters exposing remotely described MCP tools through Corki's registry."""

from __future__ import annotations

import re
from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
from time import perf_counter
from typing import Any

import httpx

from corki.mcp.client import MCPClient, MCPProtocolError, validate_tool_result
from corki.mcp.connection import MCPConnection, MCPServerMetadata
from corki.mcp.output import mcp_output
from corki.protocol.tools import (
    ToolCall,
    ToolConcurrency,
    ToolExposure,
    ToolResult,
    ToolSpec,
)
from corki.protocol.truncation import TruncationPolicy
from corki.tools import ToolContext

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_-]")


def exposed_tool_name(server: str, remote: str) -> str:
    value = f"mcp__{_SAFE_NAME.sub('_', server)}__{_SAFE_NAME.sub('_', remote)}"
    if len(value) <= 64:
        return value
    return f"{value[:47]}_{sha256(value.encode()).hexdigest()[:16]}"


class MCPTool:
    # Both search metadata and schema are immutable for this handler's lifetime.
    # Connection routing may change independently at MCP call admission.
    immutable_search_metadata = True

    def __init__(
        self,
        server_name: str,
        definition: Mapping[str, Any],
        client: MCPClient | MCPConnection,
        *,
        exposure: ToolExposure = ToolExposure.DIRECT,
        call_router=None,
        server_instructions: str | None = None,
        server_metadata: MCPServerMetadata | None = None,
    ) -> None:
        remote_name = definition.get("name")
        if not isinstance(remote_name, str) or not remote_name:
            raise ValueError("MCP tool is missing a name")
        description = definition.get("description")
        schema = definition.get("inputSchema", {"type": "object", "properties": {}})
        if not isinstance(schema, Mapping):
            raise ValueError(f"MCP tool {remote_name} has an invalid input schema")
        annotations = definition.get("annotations", {})
        read_only = isinstance(annotations, Mapping) and annotations.get("readOnlyHint") is True
        self.remote_name = remote_name
        self._server_name = server_name
        self._call_router = call_router
        self._client = client
        self._server_metadata = server_metadata or MCPServerMetadata()
        # Regular MCP servers cannot claim hosted connector/plugin provenance
        # through tool._meta. Namespace metadata comes from initialize only.
        source_description = (server_instructions or "").strip() or None
        properties = schema.get("properties")
        search_parts = [
            exposed_tool_name(server_name, remote_name),
            remote_name,  # callable name; intentionally repeated for regular MCP
            remote_name,  # original wire name
            server_name,
        ]
        for part in (definition.get("title"), description, source_description):
            if isinstance(part, str) and part.strip():
                search_parts.append(part.strip())
        if isinstance(properties, Mapping):
            search_parts.extend(sorted(properties))
        self._spec = ToolSpec(
            exposed_tool_name(server_name, remote_name),
            str(description or f"MCP tool {remote_name} from {server_name}")[:1_024],
            dict(schema),
            exposure=exposure,
            source=server_name.strip() or None,
            source_description=source_description,
            search_text=" ".join(search_parts),
            concurrency=ToolConcurrency.PARALLEL if read_only else ToolConcurrency.EXCLUSIVE,
        )

    @property
    def spec(self) -> ToolSpec:
        return deepcopy(self._spec)

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        assert call.arguments is not None
        policy = context.model_output_policy
        started = perf_counter()

        def capture(limit: int | None) -> None:
            nonlocal policy, started
            policy = (
                TruncationPolicy("tokens", limit)
                if limit is not None
                else context.model_output_policy
            )
            started = perf_counter()

        try:
            if self._call_router is None:
                if isinstance(self._client, MCPConnection):
                    result = await self._client.call_tool(
                        self.remote_name,
                        call.arguments,
                        on_external_context=context.on_external_context,
                        on_output_token_limit=capture,
                    )
                else:
                    settings = getattr(self._client, "settings", None)
                    capture(
                        dict(getattr(settings, "tool_output_token_limits", ())).get(
                            self.remote_name
                        )
                    )
                    if (
                        self._server_metadata.pollutes_memory
                        and context.on_external_context is not None
                    ):
                        await context.on_external_context()
                    result = await self._client.call_tool(self.remote_name, call.arguments)
            else:
                result = await self._call_router(
                    self._server_name,
                    self.remote_name,
                    call.arguments,
                    on_external_context=context.on_external_context,
                    on_output_token_limit=capture,
                )
            result = validate_tool_result(result)
        except (MCPProtocolError, httpx.HTTPError, TimeoutError, OSError) as error:
            # An admitted remote failure is an MCP error value, not a JS host
            # dispatch failure. Cancellation deliberately bypasses this boundary.
            message = f"{type(error).__name__}: {error}"
            if isinstance(error, (TimeoutError, httpx.TimeoutException)):
                message = (
                    "MCP tool timed out; execution outcome may be unknown. "
                    "Do not automatically retry an operation with side effects. " + message
                )
            result = {"content": [{"type": "text", "text": message}], "isError": True}
        return mcp_output(
            call,
            result,
            context=context,
            policy=policy,
            wall_time=perf_counter() - started,
        )
