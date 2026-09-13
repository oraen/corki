import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from corki.context.tokens import estimate_item_tokens, estimate_request_tokens, estimate_text_tokens
from corki.protocol.ids import ToolCallId, new_turn_id
from corki.protocol.items import ToolResultItem
from corki.protocol.tools import ToolCall, ToolExposure, ToolResult, ToolSpec
from corki.tools import ToolContext, ToolExecutor, ToolRegistry
from corki.tools.discovery import build_tool_plan, current_discovery_history
from corki.tools.search import ToolSearchIndex, ToolSearchTool


class FixtureTool:
    def __init__(self, spec):
        self.spec = spec
        self.calls = 0

    async def execute(self, call, context):
        self.calls += 1
        return ToolResult(call.id, call.name, "ran")


def test_freeform_search_preserves_internal_spec_but_budgets_ordinary_schema(tmp_path):
    async def scenario():
        spec = ToolSpec(
            "raw",
            "raw source",
            {},
            exposure=ToolExposure.DEFERRED,
            input_kind="freeform",
            freeform_format={"type": "grammar", "syntax": "lark", "definition": "x" * 40_000},
        )
        small = replace(spec, freeform_format={"type": "text"})
        assert spec.as_chat_completion_tool() == small.as_chat_completion_tool()
        assert estimate_request_tokens("", (), (spec,)) == estimate_request_tokens("", (), (small,))
        item = ToolResultItem(
            ToolCallId("s"), "tool_search", "short", new_turn_id(), discovered_tools=(spec,)
        )
        assert estimate_item_tokens(item) == estimate_item_tokens(
            replace(item, discovered_tools=())
        )
        registry = ToolRegistry()
        registry.register(FixtureTool(spec))
        registry.register(ToolSearchTool(registry))
        result = await ToolExecutor(registry, output_char_budget=500).execute(
            ToolCall(ToolCallId("s"), "tool_search", {"query": "raw"}), ToolContext(tmp_path)
        )
        assert not result.is_error and result.discovered_tools == (spec,)
        assert json.loads(result.content) == {"tools": [spec.as_chat_completion_tool()]}
        assert "grammar" not in result.content
        visible = replace(item, content=result.content)
        assert estimate_item_tokens(visible) >= estimate_text_tokens(result.content)
        assert estimate_item_tokens(visible) > estimate_item_tokens(item)

    asyncio.run(scenario())


def test_search_indexes_recursive_metadata_top_k_and_definition_changes():
    first = ToolSpec("alpha", "unrelated", {"type": "object"}, exposure=ToolExposure.DEFERRED)
    second = ToolSpec(
        "beta",
        "another",
        {
            "type": "object",
            "properties": {
                "nested": {
                    "type": "array",
                    "items": {"anyOf": [{"description": "calendar"}]},
                }
            },
        },
        exposure=ToolExposure.DEFERRED,
    )
    index = ToolSearchIndex()
    assert index.search((first, second), "calendar", 1) == (second,)
    generation = index.generation
    assert index.search((first, second), "calendar", 8) == (second,)
    assert index.generation == generation
    assert index.search((first, second), "no_match_xyz", 8) == ()
    changed = replace(first, description="calendar calendar calendar")
    assert index.search((changed, second), "calendar", 1) == (changed,)
    assert index.generation == generation + 1
    assert index.search((first,), "calendar", 8) == ()


@pytest.mark.parametrize("query,limit", [(" ", 8), ("calendar", 0), ("calendar", True)])
def test_invalid_search_parameters_return_observation(tmp_path, query, limit):
    async def scenario():
        registry = ToolRegistry()
        registry.register(ToolSearchTool(registry))
        result = await ToolExecutor(registry, output_char_budget=500).execute(
            ToolCall(ToolCallId("search"), "tool_search", {"query": query, "limit": limit}),
            ToolContext(tmp_path),
        )
        assert result.is_error
        assert not result.discovered_tools

    asyncio.run(scenario())


def test_exposure_filter_preserves_complete_matching_schemas(tmp_path: Path):
    async def scenario():
        registry = ToolRegistry()
        for exposure in ToolExposure:
            registry.register(
                FixtureTool(
                    ToolSpec(
                        exposure.value,
                        "calendar",
                        {"type": "object"},
                        exposure=exposure,
                    )
                )
            )
        registry.register(
            FixtureTool(
                ToolSpec(
                    "oversized",
                    "calendar " * 20_000,
                    {"type": "object"},
                    exposure=ToolExposure.DEFERRED,
                )
            )
        )
        registry.register(ToolSearchTool(registry))
        registry.seal()
        result = await ToolExecutor(registry, output_char_budget=10).execute(
            ToolCall(ToolCallId("search"), "tool_search", {"query": "calendar"}),
            ToolContext(tmp_path),
        )
        assert not result.is_error
        assert {spec.name for spec in result.discovered_tools} == {
            "deferred",
            "deferred_model_only",
            "oversized",
        }
        assert len(json.loads(result.content)["tools"]) == 3
        assert estimate_text_tokens(result.content) > 10_000

    asyncio.run(scenario())


def test_stale_discovery_cannot_load_changed_removed_or_hidden_tool():
    original = ToolSpec("lookup", "calendar", {}, exposure=ToolExposure.DEFERRED)
    result = ToolResultItem(
        ToolCallId("search"),
        "tool_search",
        "loaded",
        new_turn_id(),
        discovered_tools=(original,),
    )
    assert [
        spec.name for spec in build_tool_plan((original,), (result,), "compatible").advertised
    ] == ["lookup"]
    for specs in (
        (),
        (replace(original, description="changed"),),
        (replace(original, exposure=ToolExposure.HIDDEN),),
    ):
        assert not build_tool_plan(specs, (result,), "compatible").advertised
        request_view = current_discovery_history(specs, (result,))
        assert request_view[0].discovered_tools == ()
        assert "search again" in request_view[0].content
        assert result.discovered_tools == (original,), "raw history was mutated"
    assert not build_tool_plan((original,), (), "compatible").advertised
    assert (
        build_tool_plan((original,), (result,), "native").advertised
        == build_tool_plan((original,), (result,), "compatible").advertised
    )
    assert build_tool_plan((original,), (result,), "native").dispatch == (original,)


def test_seal_and_request_specs_are_isolated_from_nested_schema_mutation(tmp_path):
    async def scenario():
        schema = {"type": "object", "properties": {"text": {"type": "string"}}}
        tool = FixtureTool(ToolSpec("echo", "echo", schema))
        registry = ToolRegistry()
        registry.register(tool)
        registry.seal()
        frozen = registry.spec("echo")
        schema["properties"]["text"]["type"] = "integer"
        tool.spec.parameters["properties"]["text"]["type"] = "integer"
        exported = registry.model_visible_specs()[0]
        exported.parameters["properties"]["text"]["type"] = "integer"
        assert registry.spec("echo") == frozen
        result = await ToolExecutor(registry, output_char_budget=500).execute(
            ToolCall(ToolCallId("call"), "echo", {"text": "valid for frozen schema"}),
            ToolContext(tmp_path),
            spec=frozen,
        )
        assert not result.is_error
        changed = replace(frozen, description="old generation")
        refused = await ToolExecutor(registry, output_char_budget=500).execute(
            ToolCall(ToolCallId("call2"), "echo", {"text": "ignored"}),
            ToolContext(tmp_path),
            spec=changed,
        )
        assert refused.is_error and tool.calls == 1

    asyncio.run(scenario())
