"""Request-scoped discovery plans reconstructed from canonical active history."""

import json
from dataclasses import dataclass, replace

from corki.protocol.items import ConversationItem, ToolResultItem
from corki.protocol.tools import ToolExposure, ToolSpec

TOOL_SEARCH_NAME = "tool_search"


def _loaded_definition_matches(current: ToolSpec | None, loaded: ToolSpec | None) -> bool:
    if current is None or loaded is None:
        return False
    # These runtime-only fields are absent from the model's loaded definition.
    # Keep identity/schema/exposure checks; dispatch always uses the current spec.
    return (
        replace(
            loaded, concurrency=current.concurrency, output_char_budget=current.output_char_budget
        )
        == current
    )


def current_discovery_history(
    specs: tuple[ToolSpec, ...], items: tuple[ConversationItem, ...], *, mode: str = "compatible"
) -> tuple[ConversationItem, ...]:
    """Preserve native history; project compatible loaded definitions without editing storage."""

    if mode == "native":
        # Codex history normalization does not consult the current tool router.
        # Removed/changed handlers affect dispatch, not past search observations.
        return items

    current = {spec.name: spec for spec in specs if spec.exposure.is_deferred}
    result = []
    for item in items:
        if isinstance(item, ToolResultItem) and item.tool_name == TOOL_SEARCH_NAME:
            valid = tuple(
                spec
                for spec in item.discovered_tools
                if _loaded_definition_matches(current.get(spec.name), spec)
            )
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
    specs: tuple[ToolSpec, ...],
    items: tuple[ConversationItem, ...],
    mode: str,
    *,
    tool_mode: str = "direct",
) -> ToolPlan:
    """Load only definitions actually discovered in the active context window.

    Native Responses obtains definitions from search-output history, not from
    top-level tools. Compatible providers require explicit schema advertisement.
    Stale or removed definitions never authorize the replacement tool.
    """

    if tool_mode == "code_mode_only":
        specs = tuple(
            spec
            for spec in specs
            if spec.name in {"exec", "wait", TOOL_SEARCH_NAME}
            or spec.exposure in {ToolExposure.DIRECT_MODEL_ONLY, ToolExposure.DEFERRED_MODEL_ONLY}
        )
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
    loaded = tuple(
        spec for spec in deferred if _loaded_definition_matches(spec, discovered.get(spec.name))
    )
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
