"""Codex search_tool.rs flow mapped onto the real Corki Runtime."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from corki.config import CorkiSettings
from corki.config.managed_mcp import MCPRequirementsLayer, compose_mcp_requirements
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted, OpenAIResponsesModel, resolve_capabilities
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import ToolCallId, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolExposure, ToolResult, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolRegistry
from corki.tools.search import ToolSearchTool


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
@pytest.mark.parametrize("catalog_change", ["unchanged", "description", "schema"])
@pytest.mark.parametrize("change_policy", [False, True])
def test_search_ledger_resume_restores_definitions_without_reexecution(
    tmp_path: Path,
    append_result: bool,
    checkpoint: bool,
    catalog_change: str,
    monkeypatch,
    change_policy: bool,
) -> None:
    catalog_changed = catalog_change != "unchanged"

    class NullSink:
        async def emit(self, event):
            pass

    class ResumeModel:
        def __init__(self, spec=CalendarTool.spec, reload=False, saved_request=False):
            self.requests = []
            self.spec, self.reload = spec, reload
            self.stale_attempt = reload and saved_request

        async def stream(self, request):
            self.requests.append(request)
            outputs = [item for item in request.items if isinstance(item, ToolResultItem)]
            search = next(item for item in outputs if item.tool_name == "tool_search")
            assert len([item for item in request.items if isinstance(item, UserMessageItem)]) == 1
            step, turn = new_step_id(), request.items[-1].turn_id
            if self.stale_attempt and len(self.requests) == 1:
                saved = next(spec for spec in request.tools if spec.name == self.spec.name)
                assert saved.description == CalendarTool.spec.description
                assert saved.parameters == CalendarTool.spec.parameters
                assert search.discovered_tools == (CalendarTool.spec,)
                call = ToolCall(
                    ToolCallId("stale-attempt"), self.spec.name, {"title": "must not execute"}
                )
                yield ModelCompleted((ToolCallItem(call, turn, step),))
                return
            if self.reload and len(self.requests) == 1 + self.stale_attempt:
                if self.stale_attempt:
                    failed = next(item for item in outputs if item.call_id == "stale-attempt")
                    assert failed.is_error and "definition changed" in failed.content
                assert self.spec.name not in {spec.name for spec in request.tools}
                assert search.discovered_tools == ()
                assert "search again" in search.content
                call = ToolCall(ToolCallId("fresh-search"), "tool_search", {"query": "calendar"})
                yield ModelCompleted((ToolCallItem(call, turn, step),))
                return
            assert self.spec.name in {spec.name for spec in request.tools}
            searches = [item for item in outputs if item.tool_name == "tool_search"]
            assert len(searches) == 1 + self.reload
            assert searches[-1].discovered_tools == (self.spec,)
            if len(self.requests) == 1 + self.reload + self.stale_attempt:
                call = ToolCall(
                    ToolCallId("after-resume"),
                    CalendarTool.spec.name,
                    {"title": "resumed", **({"room": "A"} if catalog_change == "schema" else {})},
                )
                yield ModelCompleted((ToolCallItem(call, turn, step),))
            else:
                assert any(item.content == "created resumed" for item in outputs)
                yield ModelCompleted((AssistantMessageItem("recovered", turn, step),))

        async def aclose(self):
            pass

    async def scenario():
        database = tmp_path / "resume.db"
        settings = CorkiSettings(working_directory=tmp_path, skills_enabled=False)
        repository = SQLiteSessionRepository(database)
        registry = ToolRegistry()
        registry.register(CalendarTool())
        warm_model = ResumeModel()
        first = LangGraphRuntime.create(
            settings=settings,
            database_path=database,
            registry=registry,
            repository=repository,
            model=warm_model,
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
        assert warm_model.requests == [], "committed search model step was sampled again"
        registry = ToolRegistry()
        calendar = CalendarTool()
        if catalog_change == "description":
            calendar.spec = replace(calendar.spec, description="Updated calendar scheduling")
        elif catalog_change == "schema":
            calendar.spec = replace(
                calendar.spec,
                parameters={
                    "type": "object",
                    "properties": {"title": {"type": "string"}, "room": {"type": "string"}},
                    "required": ["title", "room"],
                },
            )
        registry.register(calendar)
        model = ResumeModel(calendar.spec, reload=catalog_changed, saved_request=checkpoint)
        resumed = LangGraphRuntime.create(
            settings=settings,
            database_path=database,
            registry=registry,
            thread_id=first.thread_id,
            model=model,
            mcp_requirements=compose_mcp_requirements(
                (MCPRequirementsLayer("host", 'additional_developer_instructions = "NEW POLICY"'),)
            )
            if change_policy
            else None,
        )

        execute_search = ToolSearchTool.execute
        searches = []

        async def observed_search(self, call, context):
            assert catalog_changed and call.id == ToolCallId("fresh-search"), (
                "durable search was rerun"
            )
            searches.append(call.id)
            return await execute_search(self, call, context)

        monkeypatch.setattr(ToolSearchTool, "execute", observed_search)
        try:
            events = [event async for event in resumed.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), str(events[-1])
            assert len(model.requests) == 2 + catalog_changed + (catalog_changed and checkpoint)
            assert searches == ([ToolCallId("fresh-search")] if catalog_changed else [])
            assert calendar.titles == ["resumed"]
            if change_policy:
                for request in model.requests:
                    policies = [
                        item
                        for item in request.items
                        if isinstance(item, ContextItem)
                        and item.key == "managed_developer_instructions"
                    ]
                    assert len(policies) == 1 and "NEW POLICY" in policies[0].content
            history = await resumed._repository.load_items(resumed.thread_id)
            durable = [
                item
                for item in history
                if isinstance(item, ToolResultItem) and item.call_id == search_call.id
            ]
            assert len(durable) == 1
            assert durable[0].discovered_tools == (CalendarTool.spec,)
            assert [event async for event in resumed.resume_pending()] == []
            # Preparation rebuilds the process-local index, not the durable call.
            assert registry.get("tool_search").index.generation == 1
        finally:
            await resumed.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["compatible", "native"])
@pytest.mark.parametrize("refresh_after_search", [False, True])
def test_responses_search_wire_roundtrip_without_eager_schemas(
    tmp_path: Path, mode: str, refresh_after_search: bool
) -> None:
    async def scenario():
        bodies = []

        def respond(request):
            bodies.append(json.loads(request.content))
            index = len(bodies)
            if index == 1:
                item = {
                    "type": "function_call",
                    "name": "tool_search",
                    "arguments": '{"query":"calendar"}',
                }
                item.update(id="search-item", call_id="search-call")
            elif index == 2:
                if refresh_after_search:
                    replacement = CalendarTool()
                    replacement.spec = replace(
                        calendar.spec, description="Updated calendar metadata"
                    )
                    registry.replace_owned(owner, (replacement,))
                item = {
                    "type": "function_call",
                    "name": CalendarTool.spec.name,
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
        owner = registry.create_owner()
        registry.replace_owned(owner, (calendar,))
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
                model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
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
            assert bodies[0]["tools"][0]["name"] == "tool_search"
            assert any(tool["name"] == calendar.spec.name for tool in bodies[1]["tools"])
            if refresh_after_search:
                assert not any(
                    tool.get("name") == calendar.spec.name for tool in bodies[2]["tools"]
                )
            assert not any(item.get("type") == "tool_search_output" for item in bodies[1]["input"])
            assert any(item.get("output") == "created round trip" for item in bodies[2]["input"])
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())
