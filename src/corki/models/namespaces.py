"""Flat tool definitions and request-scoped reverse mappings, never leaf-name routing."""

from corki.models.base import ModelError
from corki.protocol.items import ToolResultItem
from corki.protocol.tool_names import (
    compatible_tool_name,
)


def response_tool_name(item, fallback=""):
    """Reject grouped wire identities instead of accidentally routing their leaf."""
    if item.get("namespace") not in (None, ""):
        raise ModelError(
            "native namespace responses are unsupported; return the flat function name"
        )
    return str(item.get("name") or fallback)


def request_tool_aliases(request):
    specs = {
        spec.name: spec
        for item in (*request.context_items, *request.items)
        if isinstance(item, ToolResultItem) and not item.is_error
        for spec in item.discovered_tools
    }
    specs.update((spec.name, spec) for spec in request.tools)
    aliases = {}
    for spec in specs.values():
        alias = compatible_tool_name(spec.name)
        if alias in aliases and aliases[alias] != spec.name:
            raise ModelError("tool compatibility wire name collision")
        aliases[alias] = spec.name
    return aliases


def group_tool_definitions(specs):
    return [spec.as_response_tool() for spec in specs]
