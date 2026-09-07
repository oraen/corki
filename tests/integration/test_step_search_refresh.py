import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelItemCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolExposure, ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["native", "compatible"])
@pytest.mark.parametrize("streamed", [False, True])
def test_replaced_search_handler_cannot_change_an_already_prepared_step(tmp_path, mode, streamed):
    async def scenario():
        requests, executed = [], []
        registry = ToolRegistry()
        owner = registry.create_owner()

        class Tool:
            def __init__(self, word):
                self.word = word
                self.spec = ToolSpec(
                    "lookup", word, {}, exposure=ToolExposure.DEFERRED, source=word
                )

            async def execute(self, call, context):
                executed.append(self.word)
                return ToolResult(call.id, call.name, self.word)

        old = Tool("amber")
        registry.replace_owned(owner, (old,))

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    previous = registry.get("tool_search")
                    registry.replace_owned(owner, (Tool("cobalt"),))
                    await runtime._refresh_tools()
                    assert registry.get("tool_search") is not previous
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "amber"})
                elif len(requests) == 2:
                    raw = await runtime._repository.load_items(runtime._thread_id)
                    found = next(
                        i
                        for i in raw
                        if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
                    )
                    assert found.discovered_tools == (old.spec,)
                    visible = next(
                        i
                        for i in request.items
                        if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
                    )
                    assert visible.discovered_tools == ((old.spec,) if mode == "native" else ())
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "cobalt"})
                elif len(requests) == 3:
                    call = ToolCall(new_tool_call_id(), "lookup", {})
                else:
                    assert len(requests) == 4 and executed == ["cobalt"]
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                item = ToolCallItem(call, turn, step)
                if streamed:
                    yield ModelItemCompleted(item)
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_search_mode=mode,
                api_mode="responses",
            ),
            database_path=tmp_path / "search.db",
            registry=registry,
            model=Model(),
        )
        try:
            events = [e async for e in runtime.stream("refresh after sampling begins")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
