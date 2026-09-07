"""Native Responses discovery wire shapes over provider-neutral history."""

import json
from typing import Any

from corki.models.base import ModelError
from corki.models.namespaces import group_tool_definitions
from corki.protocol.items import ConversationItem, ToolCallItem, ToolResultItem
from corki.protocol.tools import ToolSpec


def native_tool_definition(spec: ToolSpec, *, native_freeform: bool = False) -> dict[str, Any]:
    """Encode a direct function or the special client-side search tool."""

    if spec.name == "tool_search":
        return {
            "type": "tool_search",
            "execution": "client",
            "description": spec.description,
            "parameters": dict(spec.parameters),
        }
    return spec.as_response_tool(native_freeform=native_freeform)


def native_search_item(
    item: ConversationItem, *, native_freeform: bool = False, native_namespaces: bool = False
) -> dict[str, Any] | None:
    """History owns loaded schemas; never inject them into the top-level tools."""

    if isinstance(item, ToolCallItem) and item.call.name == "tool_search":
        return {
            "type": "tool_search_call",
            "call_id": str(item.call.id),
            "execution": "client",
            "status": "completed",
            "arguments": dict(item.call.arguments or {}),
        }
    if isinstance(item, ToolResultItem) and item.tool_name == "tool_search":
        definitions = group_tool_definitions(
            item.discovered_tools,
            native_freeform=native_freeform,
            native_namespaces=native_namespaces,
            discovered=True,
        )
        return {
            "type": "tool_search_output",
            "call_id": str(item.call_id),
            "execution": "client",
            "status": "completed",
            "tools": definitions if not item.is_error else [],
        }
    return None


def search_call_arguments(item: dict[str, Any]) -> tuple[str, str]:
    """Validate identity while leaving malformed arguments as model observations."""

    call_id = item.get("call_id")
    if item.get("execution") != "client" or not isinstance(call_id, str) or not call_id:
        raise ModelError("unsupported or missing client tool_search call identity")
    return call_id, json.dumps(item.get("arguments"), ensure_ascii=False)


def response_tool_name(item: dict[str, Any], fallback: str = "") -> str:
    """Only the default namespace aliases flat Corki names, never arbitrary namespaces."""

    name = str(item.get("name") or fallback)
    namespace = item.get("namespace")
    return (
        name
        if namespace in (None, "", "functions")
        else (name if name.startswith(f"{namespace}::") else f"{namespace}::{name}")
    )
