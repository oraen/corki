"""Cold loaded definitions must not confuse JSON booleans with numbers."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolExposure, ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("old,new", [(True, 1), (False, 0)])
def test_cold_schema_type_change_requires_search_before_replacement_executes(tmp_path, old, new):
    async def scenario():
        requests, executed = [], []

        class Tool:
            def __init__(self, value):
                self.spec = ToolSpec(
                    "lookup",
                    "lookup fixture",
                    {"type": "object", "properties": {"flag": {"enum": [value]}}},
                    exposure=ToolExposure.DEFERRED,
                )

            async def execute(self, call, context):
                executed.append(call.arguments)
                return ToolResult(call.id, call.name, "replacement result")

        class Model:
            async def stream(self, request):
                requests.append(request)
                index = len(requests)
                turn, step = request.items[-1].turn_id, new_step_id()
                if index in {1, 4}:
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "lookup"})
                elif index == 3:
                    assert "lookup" not in {spec.name for spec in request.tools}
                    call = ToolCall(new_tool_call_id(), "lookup", {"flag": new})
                elif index == 5:
                    result = next(
                        item
                        for item in request.items
                        if isinstance(item, ToolResultItem) and item.tool_name == "lookup"
                    )
                    assert result.is_error and not executed
                    spec = next(spec for spec in request.tools if spec.name == "lookup")
                    assert type(spec.parameters["properties"]["flag"]["enum"][0]) is int
                    call = ToolCall(new_tool_call_id(), "lookup", {"flag": new})
                else:
                    assert index in {2, 6}
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        settings = CorkiSettings(
            working_directory=tmp_path, skills_enabled=False, plugins_enabled=False
        )
        database = tmp_path / "sessions.db"
        registry = ToolRegistry()
        registry.register(Tool(old))
        warm = await LangGraphRuntime.acreate(
            settings=settings, database_path=database, registry=registry, model=Model()
        )
        try:
            assert isinstance([event async for event in warm.stream("discover")][-1], TurnCompleted)
            thread = warm.thread_id
            archive = await warm._repository.load_items(thread)
        finally:
            await warm.aclose()
        replacement = ToolRegistry()
        replacement.register(Tool(new))
        cold = await LangGraphRuntime.acreate(
            settings=settings,
            database_path=database,
            registry=replacement,
            model=Model(),
            thread_id=thread,
        )
        try:
            events = [event async for event in cold.stream("use updated tool")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 6
            assert executed == [{"flag": new}]
            assert (await cold._repository.load_items(thread))[: len(archive)] == archive
        finally:
            await cold.aclose()

    asyncio.run(scenario())
