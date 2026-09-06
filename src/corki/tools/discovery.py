"""Request-scoped discovery plans reconstructed from canonical active history."""

import json
from dataclasses import dataclass, replace

from corki.protocol.items import ConversationItem, ToolResultItem
from corki.protocol.tools import ToolExposure, ToolSpec

TOOL_SEARCH_NAME = "tool_search"


def current_discovery_history(
    specs: tuple[ToolSpec, ...], items: tuple[ConversationItem, ...]
) -> tuple[ConversationItem, ...]:
    """Invalidate stale definitions in the request view, preserving raw audit history."""

    current = {spec.name: spec for spec in specs if spec.exposure.is_deferred}
    result = []
    for item in items:
        if isinstance(item, ToolResultItem) and item.tool_name == TOOL_SEARCH_NAME:
            valid = tuple(spec for spec in item.discovered_tools if current.get(spec.name) == spec)
            if valid != item.discovered_tools:
                item = replace(
                    item,
                    discovered_tools=valid,
                    content=json.dumps(
                        {
                            "tools": [spec.as_chat_completion_tool() for spec in valid],
                            "notice": "Definitions changed or are unavailable; search again.",
                        },
                        ensure_ascii=False,
                    ),
                )
        result.append(item)
    return tuple(result)


@dataclass(frozen=True)
class ToolPlan:
    """Separate wire advertisement from eligible dispatch (not a permission grant)."""

    advertised: tuple[ToolSpec, ...]
    dispatch: tuple[ToolSpec, ...]


def build_tool_plan(
    specs: tuple[ToolSpec, ...], items: tuple[ConversationItem, ...], mode: str
) -> ToolPlan:
    """Load only definitions actually discovered in the active context window.

    Native Responses obtains definitions from search-output history, not from
    top-level tools. Compatible providers require explicit schema advertisement.
    Stale or removed definitions never authorize the replacement tool.
    """

    direct = tuple(spec for spec in specs if spec.exposure.is_model_visible)
    if mode == "disabled":
        return ToolPlan(direct, direct)
    deferred = tuple(spec for spec in specs if spec.exposure.is_deferred)
    if mode == "native":
        return ToolPlan(direct, (*direct, *deferred))
    discovered = {
        spec.name: spec
        for item in items
        if isinstance(item, ToolResultItem)
        and item.tool_name == TOOL_SEARCH_NAME
        and not item.is_error
        for spec in item.discovered_tools
    }
    loaded = tuple(spec for spec in deferred if discovered.get(spec.name) == spec)
    advertised = (
        *direct,
        *(
            replace(
                spec,
                exposure=(
                    ToolExposure.DIRECT_MODEL_ONLY
                    if spec.exposure is ToolExposure.DEFERRED_MODEL_ONLY
                    else ToolExposure.DIRECT
                ),
            )
            for spec in loaded
        ),
    )
    return ToolPlan(advertised, (*direct, *loaded))
