import asyncio
from dataclasses import replace

from corki.protocol.ids import ToolCallId
from corki.protocol.tools import ToolCall, ToolExposure, ToolResult, ToolSpec
from corki.tools import ToolContext, ToolExecutor, ToolRegistry
from corki.tools.search import ToolSearchTool


def test_search_handler_keeps_the_metadata_generation_it_advertised(tmp_path):
    class Tool:
        def __init__(self, spec):
            self.spec = spec

    original = ToolSpec("lookup", "amber", {}, exposure=ToolExposure.DEFERRED, source="docs")
    registry = ToolRegistry()
    owner = registry.create_owner()
    registry.replace_owned(owner, (Tool(original),))
    search = ToolSearchTool(registry)
    registry.seal()
    registry.replace_owned(owner, (Tool(replace(original, description="cobalt", source="notes")),))

    async def scenario():
        result = await search.execute(
            ToolCall(ToolCallId("search"), "tool_search", {"query": "amber"}), ToolContext(tmp_path)
        )
        assert result.discovered_tools == (original,)
        assert "- docs\n" in search.spec.description and "notes" not in search.spec.description
        assert search.index.generation == 1

    asyncio.run(scenario())


def test_executor_owns_search_definition_snapshot_and_errors_do_not_load(tmp_path):
    async def scenario():
        definition = ToolSpec(
            "lookup",
            "amber",
            {"properties": {"key": {"type": "string"}}},
            exposure=ToolExposure.DEFERRED,
        )
        error = False

        class Search(ToolSearchTool):
            async def execute(self, call, context):
                return ToolResult(
                    call.id,
                    call.name,
                    "definitions",
                    is_error=error,
                    discovered_tools=(definition,),
                )

        registry = ToolRegistry()
        registry.register(Search(registry))
        executor = ToolExecutor(registry, output_char_budget=1000)
        call = ToolCall(ToolCallId("search"), "tool_search", {"query": "amber"})
        result = await executor.execute(call, ToolContext(tmp_path))
        definition.parameters["properties"]["key"]["type"] = "integer"
        assert result.discovered_tools[0].parameters["properties"]["key"]["type"] == "string"
        error = True
        result = await executor.execute(call, ToolContext(tmp_path))
        assert result.is_error and result.discovered_tools == ()

    asyncio.run(scenario())


def test_discovery_metadata_has_raw_transport_budget_even_with_short_content(tmp_path, monkeypatch):
    async def scenario():
        definition = ToolSpec("lookup", "x" * 1000, {}, exposure=ToolExposure.DEFERRED)

        class Search(ToolSearchTool):
            async def execute(self, call, context):
                return ToolResult(call.id, call.name, "short", discovered_tools=(definition,))

        registry = ToolRegistry()
        registry.register(Search(registry))
        monkeypatch.setattr("corki.tools.executor.MAX_RAW_TOOL_RESULT_BYTES", 800)
        result = await ToolExecutor(registry, output_char_budget=1000).execute(
            ToolCall(ToolCallId("search"), "tool_search", {"query": "amber"}),
            ToolContext(tmp_path),
        )
        assert result.is_error and result.discovered_tools == ()
        assert "definitions exceed raw transport limit" in result.content

    asyncio.run(scenario())
