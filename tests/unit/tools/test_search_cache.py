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
