"""Codex search_tool.rs flow mapped onto the real Corki Runtime."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted, OpenAIResponsesModel, resolve_capabilities
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import ToolCallId, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolExposure, ToolResult, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolRegistry


class CalendarTool:
    spec = ToolSpec(
        "create_calendar_event",
        "Schedule a calendar meeting with a title",
        {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
        exposure=ToolExposure.DEFERRED,
    )

    def __init__(self):
        self.titles = []

    async def execute(self, call, context):
        self.titles.append(call.arguments["title"])
        return ToolResult(call.id, call.name, "created " + call.arguments["title"])


class DiscoveryModel:
    def __init__(self):
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        turn_id, step_id = request.items[-1].turn_id, new_step_id()
        names = {tool.name for tool in request.tools}
        index = len(self.requests)
        if index == 1:
            assert "tool_search" in names, "deferred tools have no discovery entry point"
            assert CalendarTool.spec.name not in names, "deferred schema was eagerly advertised"
            call = ToolCall(ToolCallId("search"), "tool_search", {"query": "calendar meeting"})
        elif index == 2:
            assert CalendarTool.spec.name in names, "search did not load a callable definition"
            result = next(item for item in request.items if isinstance(item, ToolResultItem))
            assert CalendarTool.spec.name in result.content
            call = ToolCall(
                ToolCallId("calendar"), CalendarTool.spec.name, {"title": "team meeting"}
            )
        else:
            assert CalendarTool.spec.name in names, "loaded tool was lost across step/turn"
            assert any(
                isinstance(item, ToolResultItem) and item.content == "created team meeting"
                for item in request.items
            )
            yield ModelCompleted((AssistantMessageItem("done", turn_id, step_id),))
            return
        yield ModelCompleted((ToolCallItem(call, turn_id, step_id),))

    async def aclose(self):
        pass


def test_search_load_call_observation_and_cross_turn_history(tmp_path: Path) -> None:
    async def scenario():
        registry, calendar, model = ToolRegistry(), CalendarTool(), DiscoveryModel()
        registry.register(calendar)
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "search.db",
            registry=registry,
            model=model,
        )
        try:
            first = [event async for event in runtime.stream("schedule a meeting")]
            assert isinstance(first[-1], TurnCompleted), first[-1]
            second = [event async for event in runtime.stream("which tool did you use?")]
            assert isinstance(second[-1], TurnCompleted), second[-1]
            assert calendar.titles == ["team meeting"]
            assert len(model.requests) == 4
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("append_result,checkpoint", [(False, False), (True, False), (True, True)])
def test_search_ledger_resume_restores_definitions_without_reexecution(
    tmp_path: Path, append_result: bool, checkpoint: bool
) -> None:
    class NullSink:
        async def emit(self, event):
            pass

    class ResumeModel:
        def __init__(self):
            self.requests = []

        async def stream(self, request):
            self.requests.append(request)
            assert CalendarTool.spec.name in {spec.name for spec in request.tools}
            outputs = [item for item in request.items if isinstance(item, ToolResultItem)]
            search = next(item for item in outputs if item.tool_name == "tool_search")
            assert search.discovered_tools == (CalendarTool.spec,)
            assert len([item for item in outputs if item.tool_name == "tool_search"]) == 1
            assert len([item for item in request.items if isinstance(item, UserMessageItem)]) == 1
            step, turn = new_step_id(), request.items[-1].turn_id
            if len(self.requests) == 1:
                call = ToolCall(
                    ToolCallId("after-resume"), CalendarTool.spec.name, {"title": "resumed"}
                )
                yield ModelCompleted((ToolCallItem(call, turn, step),))
            else:
                yield ModelCompleted((AssistantMessageItem("recovered", turn, step),))

        async def aclose(self):
            pass

    async def scenario():
        database = tmp_path / "resume.db"
        settings = CorkiSettings(working_directory=tmp_path, skills_enabled=False)
        repository = SQLiteSessionRepository(database)
        registry = ToolRegistry()
        registry.register(CalendarTool())
        first = LangGraphRuntime.create(
            settings=settings,
            database_path=database,
            registry=registry,
            repository=repository,
            model=ResumeModel(),
        )
        await first._ensure_ready()
        turn = new_turn_id()
        user = UserMessageItem("discover calendar", turn)
        await repository.save_turn(
            TurnRecord(turn, first.thread_id, TurnStatus.RUNNING, user.content)
        )
        await repository.append_items(first.thread_id, (user,))
        search_call = ToolCall(ToolCallId("durable-search"), "tool_search", {"query": "calendar"})
        await repository.commit_model_step(
            first.thread_id,
            turn,
            0,
            ModelCompleted((ToolCallItem(search_call, turn, new_step_id()),)),
        )
        state = _initial_state(first.thread_id, turn, settings, user)
        state["request_tools"] = registry.model_visible_specs()
        node_runtime = type("NodeRuntime", (), {"context": GraphRunContext(events=NullSink())})()
        result = await first._graph._execute_one(state, node_runtime, search_call)
        if append_result:
            await repository.append_items(first.thread_id, (result,))
        if checkpoint:
            reached_model = asyncio.Event()

            class BlockingLoadedModel:
                async def stream(self, request):
                    assert CalendarTool.spec.name in {spec.name for spec in request.tools}
                    reached_model.set()
                    await asyncio.Event().wait()
                    yield

            first._graph._model = BlockingLoadedModel()
            invocation = asyncio.create_task(
                first._compiled.ainvoke(
                    state,
                    context=node_runtime.context,
                    config=first._graph_config(turn),
                )
            )
            try:
                await asyncio.wait_for(reached_model.wait(), timeout=3)
                saved = await first._checkpointer.aget_tuple(first._graph_config(turn))
                assert saved is not None
                values = saved.checkpoint["channel_values"]
                assert CalendarTool.spec.name in {spec.name for spec in values["request_tools"]}
            finally:
                invocation.cancel()
                await asyncio.gather(invocation, return_exceptions=True)
        await first.aclose()
        registry = ToolRegistry()
        calendar = CalendarTool()
        registry.register(calendar)
        model = ResumeModel()
        resumed = LangGraphRuntime.create(
            settings=settings,
            database_path=database,
            registry=registry,
            thread_id=first.thread_id,
            model=model,
        )
        try:
            events = [event async for event in resumed.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(model.requests) == 2
            assert calendar.titles == ["resumed"]
            assert registry.get("tool_search").index.generation == 0, "durable search was rerun"
        finally:
            await resumed.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["compatible", "native"])
def test_responses_search_wire_roundtrip_without_eager_schemas(tmp_path: Path, mode: str) -> None:
    async def scenario():
        bodies = []

        def respond(request):
            bodies.append(json.loads(request.content))
            index = len(bodies)
            if index == 1:
                item = (
                    {
                        "type": "tool_search_call",
                        "execution": "client",
                        "arguments": {"query": "calendar"},
                    }
                    if mode == "native"
                    else {
                        "type": "function_call",
                        "name": "tool_search",
                        "arguments": '{"query":"calendar"}',
                    }
                )
                item.update(id="search-item", call_id="search-call")
            elif index == 2:
                item = {
                    "type": "function_call",
                    "name": CalendarTool.spec.name,
                    "namespace": "functions",
                    "id": "event-item",
                    "call_id": "event-call",
                    "arguments": '{"title":"round trip"}',
                }
            else:
                item = {"type": "message", "content": [{"type": "output_text", "text": "done"}]}
            packets = [
                {"type": "response.output_item.done", "item": item},
                {
                    "type": "response.completed",
                    "response": {"id": f"response-{index}", "output": [item]},
                },
            ]
            return httpx.Response(200, text="".join(f"data: {json.dumps(p)}\n\n" for p in packets))

        registry, calendar = ToolRegistry(), CalendarTool()
        registry.register(calendar)
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        model = OpenAIResponsesModel(
            api_key="test",
            base_url="https://example.test/v1",
            client=client,
            capabilities=replace(
                resolve_capabilities(base_url="https://example.test/v1", api_mode="responses"),
                supports_native_tool_search=mode == "native",
            ),
        )
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                api_mode="responses",
                tool_search_mode=mode,
                skills_enabled=False,
            ),
            database_path=tmp_path / "wire.db",
            registry=registry,
            model=model,
        )
        try:
            events = [event async for event in runtime.stream("create a calendar event")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert calendar.titles == ["round trip"]
            assert len(bodies) == 3
            assert not any(tool.get("name") == calendar.spec.name for tool in bodies[0]["tools"])
            if mode == "native":
                assert bodies[0]["tools"][0]["type"] == "tool_search"
                output = next(
                    item for item in bodies[1]["input"] if item.get("type") == "tool_search_output"
                )
                assert output["tools"][0]["name"] == "functions"
                function = output["tools"][0]["tools"][0]
                assert function["name"] == calendar.spec.name
                assert function["parameters"] == calendar.spec.parameters
                assert function["defer_loading"] is True
                assert all(
                    not any(tool.get("name") == calendar.spec.name for tool in body["tools"])
                    for body in bodies
                )
            else:
                assert bodies[0]["tools"][0]["name"] == "tool_search"
                assert any(tool["name"] == calendar.spec.name for tool in bodies[1]["tools"])
                assert not any(
                    item.get("type") == "tool_search_output" for item in bodies[1]["input"]
                )
            assert any(item.get("output") == "created round trip" for item in bodies[2]["input"])
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())
