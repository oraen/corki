"""Metadata from an exact prepared MCP definition, not the model's old Step schema."""

from dataclasses import dataclass
from typing import Any

from corki.mcp.approvals import MCPToolAnnotations
from corki.mcp.projection import MCPToolProjection


@dataclass(frozen=True, slots=True)
class MCPCallMetadata:
    annotations: MCPToolAnnotations
    connector_id: str | None
    connector_name: str | None
    connector_description: str | None
    link_id: str | None
    connected_account_email: str | None
    tool_title: str | None
    tool_description: str | None
    codex_apps_meta: dict[str, Any] | None


def call_metadata(projection: MCPToolProjection, arguments: Any) -> MCPCallMetadata:
    definition = projection.definition
    return MCPCallMetadata(
        MCPToolAnnotations.from_mapping(definition.get("annotations")),
        projection.identity.connector_id,
        projection.connector_name,
        None,
        None,
        None,
        definition.get("title"),
        definition.get("description"),
        None,
    )
