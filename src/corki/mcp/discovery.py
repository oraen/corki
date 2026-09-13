"""Detached non-model metadata; discovery does not confer callable readiness."""

from dataclasses import dataclass
from typing import Any

from corki.mcp.catalog import MCPCatalogSource
from corki.mcp.connection import MCPServerMetadata


@dataclass(frozen=True, slots=True)
class MCPToolCatalogEntry:
    name: str
    server_name: str
    definition: dict[str, Any]
    server_instructions: str | None
    source: MCPCatalogSource | None
    metadata: MCPServerMetadata
    connector_id: str | None = None
    connector_name: str | None = None
    namespace_description: str | None = None
