from dataclasses import replace

import pytest

from corki.models.tool_search import native_search_item
from corki.protocol.ids import ToolCallId, new_turn_id
from corki.protocol.items import ToolResultItem, item_to_payload
from corki.protocol.tools import ToolConcurrency, ToolExposure, ToolSpec
from corki.tools.discovery import build_tool_plan, current_discovery_history


@pytest.mark.parametrize("kind", ["json", "freeform"])
def test_execution_settings_do_not_unload_compatible_definition(kind):
    old = ToolSpec("lookup", "Find records", {}, exposure=ToolExposure.DEFERRED, input_kind=kind)
    current = replace(old, concurrency=ToolConcurrency.PARALLEL, output_char_budget=80)
    result = ToolResultItem(
        ToolCallId("s"),
        "tool_search",
        "original search result",
        new_turn_id(),
        discovered_tools=(old,),
    )
    before = item_to_payload(result)
    plan = build_tool_plan((current,), (result,), "compatible")
    assert plan.dispatch == (current,)
    assert plan.advertised == (replace(current, exposure=ToolExposure.DIRECT),)
    assert current_discovery_history((current,), (result,)) == (result,)
    assert item_to_payload(result) == before


@pytest.mark.parametrize("change", ["schema", "removed", "hidden", "metadata"])
def test_native_history_remains_original_while_dispatch_tracks_current_registry(change):
    old = ToolSpec("lookup", "Find records", {}, exposure=ToolExposure.DEFERRED)
    result = ToolResultItem(
        ToolCallId("s"), "tool_search", "original", new_turn_id(), discovered_tools=(old,)
    )
    specs = (
        ()
        if change == "removed"
        else (
            replace(
                old,
                **{
                    "schema": {"parameters": {"type": "object", "required": ["new_argument"]}},
                    "hidden": {"exposure": ToolExposure.HIDDEN},
                    "metadata": {"search_text": "new search words"},
                }[change],
            ),
        )
    )
    before = native_search_item(result, native_freeform=True)
    view = current_discovery_history(specs, (result,), mode="native")
    assert view == (result,)
    assert native_search_item(view[0], native_freeform=True) == before
    plan = build_tool_plan(specs, view, "native")
    assert plan.advertised == ()
    assert plan.dispatch == (() if change in {"removed", "hidden"} else specs)


@pytest.mark.parametrize(
    "changes",
    [
        {"parameters": {"type": "object", "required": ["new"]}},
        {"name": "other"},
        {"description": "new description"},
        {"source": "different identity"},
        {"exposure": ToolExposure.HIDDEN},
        {"input_kind": "freeform"},
    ],
)
def test_compatible_matching_still_rejects_changed_definitions_and_identities(changes):
    old = ToolSpec("lookup", "Find records", {}, exposure=ToolExposure.DEFERRED)
    current = replace(old, concurrency=ToolConcurrency.PARALLEL, output_char_budget=80, **changes)
    result = ToolResultItem(
        ToolCallId("s"), "tool_search", "original", new_turn_id(), discovered_tools=(old,)
    )
    assert build_tool_plan((current,), (result,), "compatible").dispatch == ()
    assert current_discovery_history((current,), (result,))[0].discovered_tools == ()
