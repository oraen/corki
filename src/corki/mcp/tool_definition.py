"""Shared admission checks before either live registration or catalog caching."""

from collections.abc import Mapping
from typing import Any

from corki.mcp.approvals import MCPToolAnnotations


def tool_is_model_visible(definition: Mapping[str, Any]) -> bool:
    """Match the native MCP Apps visibility predicate, including malformed defaults."""
    metadata = definition.get("_meta")
    ui = metadata.get("ui") if isinstance(metadata, Mapping) else None
    visibility = ui.get("visibility") if isinstance(ui, Mapping) else None
    return not isinstance(visibility, list) or "model" in visibility


def validated_tool_fields(
    definition: Mapping[str, Any],
) -> tuple[str, Mapping[str, Any], MCPToolAnnotations]:
    """Do not let annotation stripping turn a malformed live catalog into a valid cache."""
    name = definition.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("MCP tool is missing a name")
    description = definition.get("description")
    if description is not None:
        if not isinstance(description, str):
            raise ValueError(f"MCP tool {name} has an invalid description")
        description.encode("utf-8")
    schema = definition.get("inputSchema", {"type": "object", "properties": {}})
    if not isinstance(schema, Mapping):
        raise ValueError(f"MCP tool {name} has an invalid input schema")
    output_schema = definition.get("outputSchema")
    if output_schema is not None and not isinstance(output_schema, Mapping):
        raise ValueError(f"MCP tool {name} has an invalid output schema")
    annotations = MCPToolAnnotations.from_mapping(definition.get("annotations", {}))
    return name, schema, annotations


def call_tool_result_schema(definition: Mapping[str, Any]) -> dict[str, Any]:
    """Internal return metadata; structuredContent deliberately remains unnormalized."""
    return {
        "type": "object",
        "properties": {
            "content": {"type": "array", "items": {"type": "object"}},
            "structuredContent": definition.get("outputSchema") or {},
            "isError": {"type": "boolean"},
            "_meta": {"type": "object"},
        },
        "required": ["content"],
        "additionalProperties": False,
    }
