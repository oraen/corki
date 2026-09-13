"""Exact-client calls retain catalog authority through preparation and execution."""

from copy import deepcopy

from corki.mcp.call_metadata import MCPCallMetadata, call_metadata
from corki.mcp.catalog_revision import MCPCatalogRevision
from corki.mcp.client import MCPProtocolError
from corki.mcp.connection import MCPConnection
from corki.mcp.projection import MCPToolProjection


class MCPPreparedCall:
    def __init__(
        self,
        connection: MCPConnection,
        name: str,
        authority: MCPCatalogRevision,
        approve,
        *,
        apply_approval,
        tool_info: MCPToolProjection,
        approval_policy: str = "never",
    ):
        self.connection = connection
        self.name = name
        self._authority = authority
        self._revision = authority.value
        self._approve = approve
        self._apply_approval = apply_approval
        self._tool_info = deepcopy(tool_info)
        self._approval_policy = approval_policy

    @property
    def approval_policy(self) -> str:
        return self._approval_policy

    @property
    def tool_info(self) -> MCPToolProjection:
        """Detached exact definition; callers cannot rewrite this call's authority."""
        return deepcopy(self._tool_info)

    def metadata_for(self, arguments) -> MCPCallMetadata:
        return call_metadata(self._tool_info, arguments)

    async def call(self, arguments, *, on_external_context=None, on_output_token_limit=None):
        result, _ = await self.call_with_metadata(
            arguments,
            on_external_context=on_external_context,
            on_output_token_limit=on_output_token_limit,
        )
        return result

    async def call_with_metadata(
        self, arguments, *, on_external_context=None, on_output_token_limit=None
    ):
        # Retain/cancel this exact client across human review, but allow catalog
        # refresh to publish while the host waits for a decision.
        with self.connection.lease():
            metadata = self.metadata_for(arguments)
            # Presentation policy also applies to rejected or failed reviews.
            if on_output_token_limit is not None:
                on_output_token_limit(
                    dict(self.connection.settings.tool_output_token_limits).get(self.name)
                )
            decision = await self._approve(arguments, metadata, None)
            async with self._authority.read() as revision:
                if revision != self._revision:
                    raise MCPProtocolError(
                        "tool call rejected because the catalog changed after "
                        f"'{self.connection.settings.name}/{self.name}' was prepared"
                    )
                result = await self.connection.call_tool(
                    self.name,
                    arguments,
                    on_external_context=on_external_context,
                    before_call=lambda: self._apply_approval(decision),
                )
                return result, metadata
