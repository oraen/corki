import asyncio
import gc
from dataclasses import replace
from weakref import ref

import pytest

from corki.mcp.tools import MCPTool
from corki.protocol.ids import ToolCallId
from corki.protocol.tools import ToolCall, ToolConcurrency, ToolExposure, ToolSpec
from corki.tools import ToolContext, ToolRegistry
from corki.tools.search_cache import ToolSearchHandlerCache


class DynamicTool:
    def __init__(self, spec):
        self.spec = spec


def mcp_tool():
    return MCPTool(
        "docs", {"name": "lookup", "description": "amber"}, object(), exposure=ToolExposure.DEFERRED
    )


def test_immutable_cache_reuses_identity_without_reloading_schema_and_does_not_retain_handlers(
    monkeypatch,
):
    registry = ToolRegistry()
    owner = registry.create_owner()
    tool = mcp_tool()
    registry.replace_owned(owner, (tool,))
    registry.seal()
    cache = ToolSearchHandlerCache()
    first = cache.get_or_build(registry)
    with monkeypatch.context() as patch:
        patch.setattr(
            registry, "spec", lambda name: pytest.fail("immutable cache hit reloaded schema")
        )
        assert cache.get_or_build(registry) is first
    weak_tool, weak_registry = ref(tool), ref(registry)
    registry.replace_owned(owner, (mcp_tool(),))
    second = cache.get_or_build(registry)
    assert second is not first and second.index is not first.index
    del tool, registry
    gc.collect()
    assert weak_tool() is None and weak_registry() is None


def test_dynamic_equivalent_instances_reuse_index_but_execution_changes_rebind_results(tmp_path):
    spec = ToolSpec("lookup", "amber", {}, exposure=ToolExposure.DEFERRED)
    registry = ToolRegistry()
    owner = registry.create_owner()
    registry.replace_owned(owner, (DynamicTool(spec),))
    registry.seal()
    cache = ToolSearchHandlerCache()
    first = cache.get_or_build(registry)
    registry.replace_owned(owner, (DynamicTool(spec),))
    assert cache.get_or_build(registry) is first
    changed = replace(spec, concurrency=ToolConcurrency.PARALLEL, output_char_budget=42)
    registry.replace_owned(owner, (DynamicTool(changed),))
    second = cache.get_or_build(registry)
    assert second is not first and second.index is first.index

    async def scenario():
        call = ToolCall(ToolCallId("s"), "tool_search", {"query": "amber"})
        assert (await first.execute(call, ToolContext(tmp_path))).discovered_tools == (spec,)
        assert (await second.execute(call, ToolContext(tmp_path))).discovered_tools == (changed,)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change",
    [
        {"name": "renamed"},
        {"description": "cobalt"},
        {"search_text": ""},
        {"parameters": {"type": "object", "properties": {"value": {"type": "integer"}}}},
        {"source": "notes"},
        {"source_description": "Changed source"},
        {"input_kind": "freeform", "freeform_format": {"type": "text"}},
    ],
)
def test_dynamic_search_information_changes_invalidate_complete_index(change):
    spec = ToolSpec("lookup", "amber", {}, exposure=ToolExposure.DEFERRED, source="docs")
    registry = ToolRegistry()
    owner = registry.create_owner()
    registry.replace_owned(owner, (DynamicTool(spec),))
    registry.seal()
    cache = ToolSearchHandlerCache()
    first = cache.get_or_build(registry)
    registry.replace_owned(owner, (DynamicTool(replace(spec, **change)),))
    second = cache.get_or_build(registry)
    assert second is not first and second.index is not first.index
    assert first.index.specs == (spec,)


def test_dynamic_schema_json_type_change_invalidates_search_handler(tmp_path):
    initial = ToolSpec(
        "lookup",
        "amber",
        {"type": "object", "properties": {"value": {"enum": [True]}}},
        exposure=ToolExposure.DEFERRED,
    )
    changed = replace(
        initial, parameters={"type": "object", "properties": {"value": {"enum": [1]}}}
    )
    registry = ToolRegistry()
    owner = registry.create_owner()
    registry.replace_owned(owner, (DynamicTool(initial),))
    cache = ToolSearchHandlerCache()
    first = cache.get_or_build(registry)

    registry.replace_owned(owner, (DynamicTool(changed),))
    second = cache.get_or_build(registry)
    assert second is not first and second.index is not first.index

    async def scenario():
        call = ToolCall(ToolCallId("s"), "tool_search", {"query": "amber"})
        assert (await first.execute(call, ToolContext(tmp_path))).discovered_tools == (initial,)
        result = await second.execute(call, ToolContext(tmp_path))
        assert result.discovered_tools[0].parameters == changed.parameters
        assert '"enum": [1]' in result.content

    asyncio.run(scenario())


def test_malformed_schema_search_metadata_does_not_block_healthy_cache_results(tmp_path):
    registry = ToolRegistry()
    owner = registry.create_owner()
    registry.replace_owned(
        owner,
        (
            DynamicTool(
                ToolSpec(
                    "broken",
                    "old data",
                    {"type": "object", "properties": [], "anyOf": None},
                    exposure=ToolExposure.DEFERRED,
                )
            ),
            DynamicTool(
                ToolSpec(
                    "lookup",
                    "calendar events",
                    {"type": "object"},
                    exposure=ToolExposure.DEFERRED,
                )
            ),
        ),
    )
    handler = ToolSearchHandlerCache().get_or_build(registry)

    async def scenario():
        result = await handler.execute(
            ToolCall(ToolCallId("search"), "tool_search", {"query": "calendar", "limit": 1}),
            ToolContext(tmp_path),
        )
        assert [spec.name for spec in result.discovered_tools] == ["lookup"]

    asyncio.run(scenario())


def test_internal_output_schema_type_change_rebinds_discovery_without_reindex(tmp_path):
    initial = ToolSpec(
        "lookup",
        "amber",
        {"type": "object"},
        exposure=ToolExposure.DEFERRED,
        output_schema={"properties": {"value": {"enum": [True]}}, "type": "object"},
    )
    changed = replace(
        initial,
        output_schema={"properties": {"value": {"enum": [1]}}, "type": "object"},
    )
    registry = ToolRegistry()
    owner = registry.create_owner()
    registry.replace_owned(owner, (DynamicTool(initial),))
    cache = ToolSearchHandlerCache()
    first = cache.get_or_build(registry)

    registry.replace_owned(owner, (DynamicTool(changed),))
    second = cache.get_or_build(registry)
    assert second is not first and second.index is first.index

    async def scenario():
        call = ToolCall(ToolCallId("s"), "tool_search", {"query": "amber"})
        old = await first.execute(call, ToolContext(tmp_path))
        new = await second.execute(call, ToolContext(tmp_path))
        assert type(old.discovered_tools[0].output_schema["properties"]["value"]["enum"][0]) is bool
        assert type(new.discovered_tools[0].output_schema["properties"]["value"]["enum"][0]) is int

    asyncio.run(scenario())


def test_failed_build_keeps_the_previous_complete_cached_handler(monkeypatch):
    spec = ToolSpec("lookup", "amber", {}, exposure=ToolExposure.DEFERRED)
    registry = ToolRegistry()
    owner = registry.create_owner()
    registry.replace_owned(owner, (DynamicTool(spec),))
    cache = ToolSearchHandlerCache()
    first = cache.get_or_build(registry)
    changed = replace(spec, description="cobalt")
    registry.replace_owned(owner, (DynamicTool(changed),))
    with monkeypatch.context() as patch:

        def fail(text):
            raise ValueError("index build failed")

        patch.setattr("corki.tools.search._tokens", fail)
        for _ in range(2):
            with pytest.raises(ValueError, match="index build failed"):
                cache.get_or_build(registry)
        registry.replace_owned(owner, (DynamicTool(spec),))
        assert cache.get_or_build(registry) is first
    registry.replace_owned(owner, (DynamicTool(changed),))
    assert cache.get_or_build(registry).index.specs == (changed,)


def test_order_exposure_and_empty_directory_are_part_of_the_cache_key():
    registry = ToolRegistry()
    owner = registry.create_owner()
    first = ToolSpec("first", "amber", {}, exposure=ToolExposure.DEFERRED)
    second = replace(first, name="second")
    cache = ToolSearchHandlerCache()
    handlers = []
    for specs in (
        (first, second),
        (second, first),
        (replace(first, exposure=ToolExposure.DIRECT), second),
        (),
    ):
        registry.replace_owned(owner, tuple(DynamicTool(spec) for spec in specs))
        handler = cache.get_or_build(registry)
        assert all(handler is not old for old in handlers)
        assert handler.index.specs == tuple(spec for spec in specs if spec.exposure.is_deferred)
        handlers.append(handler)


def test_immutable_identity_does_not_use_user_defined_equality():
    class EqualTool(DynamicTool):
        immutable_search_metadata = True

        def __eq__(self, other):
            return True

    spec = ToolSpec("lookup", "amber", {}, exposure=ToolExposure.DEFERRED)
    registry = ToolRegistry()
    owner = registry.create_owner()
    cache = ToolSearchHandlerCache()
    registry.replace_owned(owner, (EqualTool(spec),))
    first = cache.get_or_build(registry)
    registry.replace_owned(owner, (EqualTool(spec),))
    assert cache.get_or_build(registry) is not first


def test_non_weakref_extension_uses_dynamic_value_contract():
    class Slotted:
        __slots__ = ("spec",)
        immutable_search_metadata = True

        def __init__(self, spec):
            self.spec = spec

    spec = ToolSpec("lookup", "amber", {}, exposure=ToolExposure.DEFERRED)
    registry = ToolRegistry()
    owner = registry.create_owner()
    cache = ToolSearchHandlerCache()
    registry.replace_owned(owner, (Slotted(spec),))
    first = cache.get_or_build(registry)
    registry.replace_owned(owner, (Slotted(spec),))
    assert cache.get_or_build(registry) is first


def test_mcp_immutable_contract_isolates_nested_exported_schemas():
    tool = MCPTool(
        "docs",
        {"name": "lookup", "inputSchema": {"properties": {"value": {"type": "string"}}}},
        object(),
    )
    before = tool.spec
    exported = tool.spec
    exported.parameters["properties"]["value"]["type"] = "integer"
    assert tool.spec == before


@pytest.mark.parametrize("immutable", [False, True])
def test_source_listing_policy_invalidates_handler_and_preserves_search(immutable, tmp_path):
    registry = ToolRegistry()
    owner = registry.create_owner()
    spec = ToolSpec("vault::read", "amber", {}, exposure=ToolExposure.DEFERRED, source="Directory")
    tool = DynamicTool(spec)
    tool.immutable_search_metadata = immutable
    registry.replace_owned(owner, (tool,))
    cache = ToolSearchHandlerCache()
    first = cache.get_or_build(registry)
    second = cache.get_or_build(registry, include_sources=False)
    assert second is cache.get_or_build(registry, include_sources=False)
    assert first is not second and first.index is not second.index
    assert "Directory" in first.spec.description and "Directory" not in second.spec.description
    for handler in (first, second):
        assert (
            "BM25" in handler.spec.description and "list_mcp_resources" in handler.spec.description
        )
        call = ToolCall(ToolCallId("s"), "tool_search", {"query": "amber"})
        assert asyncio.run(handler.execute(call, ToolContext(tmp_path))).discovered_tools == (spec,)
    third = cache.get_or_build(registry)
    assert third is not second and third.spec == first.spec


def test_omitted_policy_survives_execution_only_rebinding():
    registry = ToolRegistry()
    owner = registry.create_owner()
    spec = ToolSpec("lookup", "amber", {}, exposure=ToolExposure.DEFERRED, source="Directory")
    registry.replace_owned(owner, (DynamicTool(spec),))
    cache = ToolSearchHandlerCache()
    first = cache.get_or_build(registry, include_sources=False)
    registry.replace_owned(owner, (DynamicTool(replace(spec, output_char_budget=42)),))
    second = cache.get_or_build(registry, include_sources=False)
    assert second.index is first.index and second is not first
    assert "Directory" not in second.spec.description
