"""The retrieved top candidate must become the tool actually callable in the loop."""

import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolExposure, ToolResult, ToolSpec
from corki.tools import ToolRegistry
from corki.tools import search as search_module


@pytest.mark.parametrize("limit,count", [(None, 8), (1, 1), (20, 12)])
def test_search_default_and_explicit_top_k_bound_loaded_definitions(tmp_path, limit, count):
    async def scenario():
        requests = []

        class Tool:
            def __init__(self, index):
                self.spec = ToolSpec(
                    f"candidate_{index}",
                    "amber",
                    {},
                    exposure=ToolExposure.DEFERRED,
                    search_text="amber",
                )

            async def execute(self, call, context):
                pytest.fail("search must not execute a candidate")

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn = request.items[-1].turn_id
                candidates = [tool for tool in request.tools if tool.name.startswith("candidate_")]
                if len(requests) == 1:
                    assert candidates == []
                    arguments = {"query": "amber"}
                    if limit is not None:
                        arguments["limit"] = limit
                    call = ToolCall(new_tool_call_id(), "tool_search", arguments)
                    yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))
                else:
                    result = [item for item in request.items if isinstance(item, ToolResultItem)][
                        -1
                    ]
                    assert not result.is_error and len(result.discovered_tools) == count
                    assert {tool.name for tool in candidates} == {
                        tool.name for tool in result.discovered_tools
                    }
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        for index in range(12):
            registry.register(Tool(index))
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, plugins_enabled=False
            ),
            database_path=tmp_path / "top-k.db",
            registry=registry,
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("find amber tools")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(requests) == 2
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_repeated_query_failures_remain_observations_without_rebuilding_index(
    tmp_path, monkeypatch
):
    async def scenario():
        requests, executed = [], []
        original = search_module._tokens
        failing = True

        def tokenize(text):
            if failing and text == "amber query":
                raise ValueError("fixture query failed")
            return original(text)

        monkeypatch.setattr(search_module, "_tokens", tokenize)

        class Tool:
            spec = ToolSpec(
                "candidate",
                "fixture",
                {"type": "object"},
                exposure=ToolExposure.DEFERRED,
                search_text="amber",
            )

            async def execute(self, call, context):
                executed.append(call.name)
                return ToolResult(call.id, call.name, "ran")

        class Model:
            async def stream(self, request):
                nonlocal failing
                requests.append(request)
                turn = request.items[-1].turn_id
                count = len(requests)
                if count in (2, 3):
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert result.is_error and "fixture query failed" in result.content
                    assert not result.discovered_tools
                    assert registry.get("tool_search").index.generation == 1
                    if count == 3:
                        failing = False
                if count <= 3:
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "amber query"})
                elif count == 4:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert not result.is_error and result.discovered_tools == (Tool.spec,)
                    assert registry.get("tool_search").index.generation == 1
                    call = ToolCall(new_tool_call_id(), "candidate", {})
                else:
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Tool())
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "failure.db",
            registry=registry,
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("search")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert executed == ["candidate"] and len(requests) == 5
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["native", "compatible"])
@pytest.mark.parametrize(
    "texts,query,chosen",
    [
        (("amber", "cobalt"), "amber cobalt cobalt", 1),
        (("amber", "cobalt"), "amber amber cobalt", 0),
        (("connections", "calendar"), "connecting", 0),
        (("café", "calendar"), "cafe", 0),
        (("🍕", "calendar"), "pizza", 0),
        (("3.14", "14"), "3.14", 0),
        (("北京", "calendar"), "bei jing", 0),
        (("the and cobalt", "calendar"), ("the and", "cobalt"), 0),
        (
            (
                "amber cobalt cobalt cobalt filler",
                "amber amber cobalt cobalt cobalt filler filler filler",
                "filler filler filler",
            ),
            "amber cobalt",
            1,
        ),
    ],
)
def test_search_ranking_loads_only_top_candidate_and_executes_it(
    tmp_path, mode, texts, query, chosen
):
    async def scenario():
        requests, executed = [], []
        queries = (query,) if isinstance(query, str) else query

        class Tool:
            def __init__(self, index, text):
                self.spec = ToolSpec(
                    f"candidate_{index}",
                    "fixture",
                    {"type": "object"},
                    exposure=ToolExposure.DEFERRED,
                    search_text=text,
                )

            async def execute(self, call, context):
                executed.append(call.name)
                return ToolResult(call.id, call.name, "selected " + call.name)

        expected = f"candidate_{chosen}"
        candidates = [Tool(index, text) for index, text in enumerate(texts)]

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn = request.items[-1].turn_id
                if len(requests) <= len(queries):
                    assert all(not t.name.startswith("candidate_") for t in request.tools)
                    if len(requests) > 1:
                        result = next(i for i in request.items if isinstance(i, ToolResultItem))
                        assert not result.is_error and not result.discovered_tools
                    call = ToolCall(
                        new_tool_call_id(),
                        "tool_search",
                        {"query": queries[len(requests) - 1], "limit": 1},
                    )
                elif len(requests) == len(queries) + 1:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert [s.name for s in result.discovered_tools] == [expected]
                    advertised = {t.name for t in request.tools if t.name.startswith("candidate_")}
                    assert advertised == {expected}
                    call = ToolCall(new_tool_call_id(), result.discovered_tools[0].name, {})
                else:
                    assert any(
                        isinstance(i, ToolResultItem) and i.content == "selected " + expected
                        for i in request.items
                    )
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        for tool in candidates:
            registry.register(tool)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_search_mode=mode,
                model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
                api_mode="responses",
            ),
            database_path=tmp_path / "ranking.db",
            registry=registry,
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("discover and execute")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert executed == [expected] and len(requests) == len(queries) + 2
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
