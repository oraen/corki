"""Detached MCP ToolInfo projection; no remote metadata grants host authority."""

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from corki.mcp.names import MCPToolIdentity

_CONNECTOR_KEYS = (
    "connector_id",
    "connector_name",
    "connector_display_name",
    "connector_description",
    "connectorDescription",
)


@dataclass(frozen=True, slots=True)
class MCPToolProjection:
    identity: MCPToolIdentity
    definition: dict[str, Any]
    connector_name: str | None
    namespace_description: str | None


def raw_call_bindings(projections: Iterable[MCPToolProjection]) -> dict[str, MCPToolProjection]:
    """Native model-identity dedup/order followed by raw-tool last-wins binding.

    Normalization sorts by raw_identity, regardless of assigned prefix/hash. A server's
    subsequence has the same order in the whole catalog, including hidden/invalid schemas.
    Visibility is checked AFTER this map is complete, never while collecting candidates.
    """
    unique = {}
    for projection in projections:
        unique.setdefault(projection.identity.raw_identity, projection)
    return {unique[key].identity.remote: unique[key] for key in sorted(unique)}


def project_tool(
    server: str, definition: Mapping[str, Any], instructions: str | None = None
) -> MCPToolProjection:
    """Use ordinary server/raw-tool identity regardless of connector metadata."""
    projected = deepcopy(dict(definition))
    remote = projected["name"]
    meta = projected.get("_meta")
    if isinstance(meta, dict):
        for key in _CONNECTOR_KEYS:
            meta.pop(key, None)
    return MCPToolProjection(
        MCPToolIdentity(server, remote, server, remote), projected, None, instructions
    )
