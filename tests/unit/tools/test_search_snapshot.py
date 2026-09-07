import asyncio
from dataclasses import replace

from corki.protocol.ids import ToolCallId
from corki.protocol.tools import ToolCall, ToolExposure, ToolSpec
from corki.tools import ToolContext, ToolRegistry
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
