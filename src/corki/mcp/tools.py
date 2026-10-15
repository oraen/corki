"""Adapters exposing remotely described MCP tools through Corki's registry."""

from __future__ import annotations

from collections.abc import Mapping
from copy import copy, deepcopy
from dataclasses import replace
from time import perf_counter
from typing import Any

import httpx

from corki.config.mcp_headers import RUST_WHITESPACE
from corki.mcp.admission import MCPAdmissionError
from corki.mcp.arguments import call_arguments
from corki.mcp.client import MCPClient, MCPProtocolError, validate_tool_result
from corki.mcp.connection import MCPConnection, MCPServerMetadata
from corki.mcp.input_schema import agent_parameters, json_bytes, mcp_input_schema
from corki.mcp.names import MCPToolName, normalize_tool_names
from corki.mcp.output import mcp_output
from corki.mcp.plugin_provenance import plugin_tool_description
from corki.mcp.projection import project_tool
from corki.mcp.tool_definition import (
    call_tool_result_schema,
    tool_is_model_visible,
    validated_tool_fields,
)
from corki.protocol.tools import (
    ToolCall,
    ToolConcurrency,
    ToolExposure,
    ToolResult,
    ToolSpec,
)
from corki.protocol.truncation import TruncationPolicy
from corki.tools import ToolContext


def exposed_tool_name(server: str, remote: str) -> str:
    """Single-tool convenience; MCPManager always normalizes the entire catalog."""
    return normalize_tool_names(((server, remote),))[0].canonical


class MCPTool:
    # Both search metadata and schema are immutable for this handler's lifetime.
    # Connection routing may change independently at MCP call admission.
    immutable_search_metadata = True

    def __init__(
        self,
        server_name: str,
        definition: Mapping[str, Any],
        client: MCPClient | MCPConnection | None,
        *,
        exposure: ToolExposure = ToolExposure.DIRECT,
        call_router=None,
        readiness=None,
        server_instructions: str | None = None,
        server_metadata: MCPServerMetadata | None = None,
        plugin_id: str | None = None,
        plugin_display_names: tuple[str, ...] = (),
        agent_plugin: bool = False,
    ) -> None:
        if client is None and call_router is None:
            raise ValueError("A cached MCP definition requires a live admission router")
        self.projection = project_tool(server_name, definition, server_instructions)
        definition = self.projection.definition
        remote_name, schema, self.approval_annotations = validated_tool_fields(definition)
        self._parameters = mcp_input_schema(schema)
        self._model_visible = tool_is_model_visible(definition)
        description = definition.get("description")
        read_only = self.approval_annotations.read_only is True
        self.remote_name = remote_name
        self._server_name = server_name
        if plugin_id is not None and (not isinstance(plugin_id, str) or not plugin_id):
            raise ValueError("MCP plugin attribution requires a nonempty host identity")
        self._plugin_id = plugin_id
        if type(agent_plugin) is not bool:
            raise ValueError("MCP Agent Plugin policy must be host-owned")
        self._agent_plugin = agent_plugin
        if (
            not isinstance(plugin_display_names, tuple)
            or any(not isinstance(name, str) for name in plugin_display_names)
            or (plugin_display_names and plugin_id is None)
        ):
            raise ValueError("MCP display names require host-owned plugin attribution")
        for name in plugin_display_names:
            name.encode("utf-8")
        plugin_display_names = tuple(sorted(set(plugin_display_names)))
        description = plugin_tool_description(description, plugin_display_names)
        self._mcp_omit_tools_from = None
        self._call_router = call_router
        self._readiness = readiness
        self._client = client
        self._server_metadata = server_metadata or MCPServerMetadata()
        # Connector metadata is Apps-only; host plugin/approval authority stays separate.
        instructions = self.projection.namespace_description or ""
        if agent_plugin:
            instructions = instructions.encode("utf-8")[:1000].decode("utf-8", errors="ignore")
        source_description = instructions.strip(RUST_WHITESPACE) or None
        properties = schema.get("properties")
        search_parts = [
            exposed_tool_name(server_name, remote_name),
            remote_name,  # callable name; intentionally repeated for regular MCP
            remote_name,  # original wire name
            server_name,
        ]
        for part in (
            definition.get("title"),
            description,
            self.projection.connector_name,
            source_description,
        ):
            if isinstance(part, str) and part.strip(RUST_WHITESPACE):
                search_parts.append(part.strip(RUST_WHITESPACE))
        search_parts.extend(
            name.strip(RUST_WHITESPACE)
            for name in plugin_display_names
            if name.strip(RUST_WHITESPACE)
        )
        if isinstance(properties, Mapping):
            search_parts.extend(sorted(properties))
        self._search_tail = tuple(search_parts[2:])
        model_description = description or ""
        if agent_plugin:
            model_description = model_description.encode("utf-8")[:1000].decode(
                "utf-8", errors="ignore"
            )
        self._spec = ToolSpec(
            exposed_tool_name(server_name, remote_name),
            model_description,
            self._parameters,
            exposure=exposure,
            source=self.projection.connector_name or server_name.strip(RUST_WHITESPACE) or None,
            source_description=source_description,
            search_text=" ".join(search_parts),
            concurrency=ToolConcurrency.PARALLEL if read_only else ToolConcurrency.EXCLUSIVE,
            output_schema=call_tool_result_schema(definition),
        )
        self._model_name = normalize_tool_names((self.projection.identity,))[0]
        self._spec = self.with_model_name(self._model_name)._spec

    def with_model_name(
        self,
        name: MCPToolName,
        *,
        exposure: ToolExposure | None = None,
        omitted_surfaces: tuple[str, ...] | None = None,
    ) -> MCPTool:
        """Bind an unpublished catalog identity without mutating a frozen handler."""
        if (name.server, name.remote) != (self._server_name, self.remote_name):
            raise ValueError("MCP callable identity does not match its raw route")
        bound = copy(self)
        bound._model_name = name
        if omitted_surfaces is not None:
            bound._mcp_omit_tools_from = tuple(omitted_surfaces)
        description = self._spec.source_description
        if not description and self.projection.connector_name:
            description = f"Tools for working with {self.projection.connector_name}."
        description = (description or "").encode("utf-8")[: 512 * 1024]
        bound._spec = replace(
            self._spec,
            name=name.canonical,
            parameters=(
                agent_parameters(self._parameters, name.leaf, self._spec.description)
                if self._agent_plugin
                else self._parameters
            ),
            exposure=(self._spec.exposure if exposure is None else exposure)
            if self._model_visible
            else ToolExposure.HIDDEN,
            namespace_description=(
                description.decode("utf-8", errors="ignore")
                if name.namespace != "functions"
                else None
            ),
            search_text=" ".join((name.flat, name.leaf, *self._search_tail)),
        )
        return bound

    def model_spec_bytes(self) -> int:
        """Bound ordinary declarations for either supported transport after name binding."""
        spec = self._spec
        return max(
            json_bytes(spec.as_response_tool()),
            json_bytes(spec.as_chat_completion_tool()),
        )

    def hidden_by_budget(self):
        bound = copy(self)
        bound._spec = replace(self._spec, exposure=ToolExposure.HIDDEN)
        # Step routing reprojects MCP exposure from this frozen host mask. The
        # budget restriction must survive model/mode changes in that projection.
        bound._mcp_omit_tools_from = ("direct", "deferred", "code_mode")
        return bound

    @property
    def model_visible(self) -> bool:
        """Remote app-only metadata restricts both declaration and model call admission."""
        return self._model_visible

    @property
    def mcp_omit_tools_from(self) -> tuple[str, ...] | None:
        """Captured host mask; None means this is not a manager-planned registration."""
        return self._mcp_omit_tools_from

    @property
    def spec(self) -> ToolSpec:
        return deepcopy(self._spec)

    @property
    def hook_tool_name(self) -> str:
        """Stable local Hook name; neither the model wire alias nor the raw RPC route."""
        name = self._model_name
        joined = (
            name.leaf
            if name.namespace == "functions"
            else name.namespace.rstrip("_") + "__" + name.leaf.lstrip("_")
        )
        return joined if joined.startswith("mcp__") else "mcp__" + joined

    @property
    def mcp_server_name(self) -> str:
        return self._server_name

    @property
    def mcp_plugin_id(self) -> str | None:
        """Winning host catalog attribution, never inferred from remote tool metadata."""
        return self._plugin_id

    def parse_call_arguments(self, call: ToolCall):
        # Own the raw parse in execute so malformed JSON becomes an MCP error
        # value, including in Code Mode, instead of a rejected host dispatch.
        # Generic tools continue validating against their advertised schema.
        return call.arguments

    async def wait_until_ready(self):
        if self._readiness is not None:
            await self._readiness(self._server_name)

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        policy = context.model_output_policy
        started = perf_counter()
        transport_error = None

        def capture(limit: int | None) -> None:
            nonlocal policy, started
            policy = (
                TruncationPolicy("tokens", limit)
                if limit is not None
                else context.model_output_policy
            )
            started = perf_counter()

        try:
            arguments = call_arguments(call)
            if self._call_router is None:
                if isinstance(self._client, MCPConnection):
                    result = await self._client.call_tool(
                        self.remote_name,
                        arguments,
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
                    result = await self._client.call_tool(self.remote_name, arguments)
            else:
                result = await self._call_router(
                    self._server_name,
                    self.remote_name,
                    arguments,
                    on_external_context=context.on_external_context,
                    on_output_token_limit=capture,
                    call_id=call.id,
                )
            result = validate_tool_result(result)
        except (
            MCPAdmissionError,
            MCPProtocolError,
            httpx.HTTPError,
            TimeoutError,
            OSError,
        ) as error:
            # Protocol/transport failures, including unavailable startup, are MCP
            # error values rather than JS host-dispatch failures. Cancellation
            # deliberately bypasses this boundary.
            # Only typed admission errors join this channel, not arbitrary
            # KeyError/ValueError bugs from a handler or host callback.
            message = f"{type(error).__name__}: {error}"
            if isinstance(error, (TimeoutError, httpx.TimeoutException)):
                message = (
                    "MCP tool timed out; execution outcome may be unknown. "
                    "Do not automatically retry an operation with side effects. " + message
                )
            result = {"content": [{"type": "text", "text": message}], "isError": True}
            transport_error = message
        return mcp_output(
            call,
            result,
            context=context,
            policy=policy,
            wall_time=perf_counter() - started,
            transport_error=transport_error,
        )
