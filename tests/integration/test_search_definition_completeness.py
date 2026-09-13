"""A search hit is a whole loadable definition, not a second budget-ranked list."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings
from corki.context import estimate_request_tokens
from corki.core import LangGraphRuntime
from corki.models import (
    ModelCompleted,
    OpenAICompatibleModel,
    OpenAIResponsesModel,
    resolve_capabilities,
)
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import ContextCompacted, TurnCompleted
from corki.protocol.ids import ToolCallId
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolExposure, ToolResult, ToolSpec
from corki.tools import ToolRegistry


class LargeDefinitionTool:
    def __init__(self, name, description_length):
        self.spec = ToolSpec(
            name,
            "A calendarium appointment tool",
            {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "x" * description_length}
                },
                "required": ["title"],
                "additionalProperties": False,
            },
            exposure=ToolExposure.DEFERRED,
            search_text="calendarium appointment",
        )
        self.calls = []

    async def execute(self, call, context):
        self.calls.append(dict(call.arguments))
        return ToolResult(call.id, call.name, "created " + call.arguments["title"])


@pytest.mark.parametrize("wire", ["chat", "responses", "native"])
@pytest.mark.parametrize("count", [1, 2])
def test_large_search_hits_load_execute_and_survive_cold_history(tmp_path, wire, count):
    async def scenario():
        # Each of two definitions fits the former8k budget on its own; their
        # union does not. One larger definition used to disappear completely.
        tools = [LargeDefinitionTool(f"calendar_{i}", 40_000 // count) for i in range(count)]
        expected = {tool.spec.name: tool.spec.parameters for tool in tools}
        bodies = []
        api_mode = "chat_completions" if wire == "chat" else "responses"

        def definition_map(body):
            functions = [t["function"] if wire == "chat" else t for t in body["tools"]]
            assert all(t.get("type") == "function" for t in body["tools"])
            return {
                tool["name"]: tool["parameters"] for tool in functions if tool["name"] in expected
            }

        def outputs(body):
            if wire == "chat":
                return [m["content"] for m in body["messages"] if m["role"] == "tool"]
            return [
                item["output"]
                for item in body["input"]
                if item.get("type") == "function_call_output"
            ]

        def respond(request):
            endpoint = "chat/completions" if wire == "chat" else "responses"
            assert str(request.url) == f"https://example.test/v1/{endpoint}"
            body = json.loads(request.content)
            bodies.append(body)
            index = len(bodies)
            if index == 1:
                assert all(name not in json.dumps(body) for name in expected)
                calls = [("search", "tool_search", {"query": "calendarium", "limit": count})]
            else:
                definitions = definition_map(body)
                assert set(definitions) == set(expected), "search omitted a matching definition"
                assert definitions == expected
                search_output = json.loads(outputs(body)[0])
                assert {
                    t["function"]["name"]: t["function"]["parameters"]
                    for t in search_output["tools"]
                } == expected
                if index == 2:
                    calls = [
                        (f"execute-{i}", tool.spec.name, {"title": f"meeting-{i}"})
                        for i, tool in enumerate(tools)
                    ]
                else:
                    assert all(f"created meeting-{i}" in outputs(body) for i in range(count))
                    calls = []
            if wire == "chat":
                delta = (
                    {
                        "tool_calls": [
                            {
                                "index": i,
                                "id": identity,
                                "type": "function",
                                "function": {"name": name, "arguments": json.dumps(arguments)},
                            }
                            for i, (identity, name, arguments) in enumerate(calls)
                        ]
                    }
                    if calls
                    else {"content": "done"}
                )
                packets = [
                    {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                    {
                        "choices": [
                            {
                                "index": 0,
                                "delta": {},
                                "finish_reason": "tool_calls" if calls else "stop",
                            }
                        ]
                    },
                ]
                return httpx.Response(
                    200,
                    text="".join(f"data: {json.dumps(p)}\n\n" for p in packets)
                    + "data: [DONE]\n\n",
                )
            items = []
            for identity, name, arguments in calls:
                item = {
                    "type": "function_call",
                    "name": name,
                    "arguments": json.dumps(arguments),
                }
                items.append({**item, "id": f"item-{identity}", "call_id": identity})
            if not items:
                items = [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "done"}],
                    }
                ]
            packets = [
                *({"type": "response.output_item.done", "item": item} for item in items),
                {"type": "response.completed", "response": {"id": f"r-{index}", "output": items}},
            ]
            return httpx.Response(200, text="".join(f"data: {json.dumps(p)}\n\n" for p in packets))

        settings = CorkiSettings(
            working_directory=tmp_path,
            model="fixture",
            model_contexts=(ModelContextInfo("fixture", supports_search_tool=True),),
            api_mode=api_mode,
            tool_search_mode="native" if wire == "native" else "compatible",
            skills_enabled=False,
            context_window_tokens=200_000,
            tool_output_char_budget=100,
        )
        thread_id = None
        for cold in (False, True):
            registry = ToolRegistry()
            for tool in tools:
                registry.register(tool)
            client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            model_type = OpenAICompatibleModel if wire == "chat" else OpenAIResponsesModel
            model = model_type(
                api_key="test",
                base_url="https://example.test/v1",
                client=client,
                capabilities=replace(
                    resolve_capabilities(base_url="https://example.test/v1", api_mode=api_mode),
                    supports_native_tool_search=wire == "native",
                ),
            )
            runtime = LangGraphRuntime.create(
                settings=settings,
                database_path=tmp_path / "sessions.db",
                registry=registry,
                model=model,
                thread_id=thread_id,
            )
            try:
                events = [
                    event
                    async for event in runtime.stream(
                        "recall previous work" if cold else "make appointments"
                    )
                ]
                assert isinstance(events[-1], TurnCompleted), events[-1]
                thread_id = runtime.thread_id
                history = await runtime._repository.load_items(thread_id)
                results = [
                    i
                    for i in history
                    if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
                ]
                assert len(results) == 1
                assert {
                    spec.name: spec.parameters for spec in results[0].discovered_tools
                } == expected
                assert all(
                    tool.calls == [{"title": f"meeting-{i}"}] for i, tool in enumerate(tools)
                )
            finally:
                await runtime.aclose()
                await client.aclose()
        assert len(bodies) == 4, "cold history should not repeat discovery or side effects"

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["compatible", "native"])
def test_large_discovery_uses_context_compaction_without_corrupting_archive(tmp_path, mode):
    tool = LargeDefinitionTool("large_calendar", 40_000)

    class Model:
        def __init__(self):
            self.requests = []

        async def stream(self, request):
            self.requests.append(request)
            turn, step = request.items[-1].turn_id, new_step_id()
            if len(self.requests) == 1:
                assert tool.spec.name not in {t.name for t in request.tools}
                yield ModelCompleted(
                    (
                        ToolCallItem(
                            ToolCall(ToolCallId("search"), "tool_search", {"query": "calendarium"}),
                            turn,
                            step,
                        ),
                    )
                )
            elif (
                isinstance(request.items[-1], UserMessageItem)
                and "checkpoint compaction" in request.items[-1].content
            ):
                assert request.tools == ()
                search = next(
                    i
                    for i in request.items
                    if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
                )
                assert search.discovered_tools == (tool.spec,)
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            "Found the calendar tool; search again if needed.", turn, step
                        ),
                    )
                )
            else:
                assert any(isinstance(i, CompactionItem) for i in request.items), (
                    "expected local compaction before the next normal sample"
                )
                assert not any(
                    isinstance(i, ToolResultItem) and i.discovered_tools for i in request.items
                )
                assert tool.spec.name not in {t.name for t in request.tools}
                assert any(
                    isinstance(i, UserMessageItem) and i.content == "CURRENT INPUT VERBATIM"
                    for i in request.items
                )
                yield ModelCompleted((AssistantMessageItem("done", turn, step),))

        async def aclose(self):
            pass

    async def scenario():
        model, registry = Model(), ToolRegistry()
        registry.register(tool)
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                model="fixture",
                model_contexts=(ModelContextInfo("fixture", supports_search_tool=True),),
                api_mode="responses",
                api_base="https://example.test/v1",
                tool_search_mode=mode,
                context_window_tokens=18_000,
                auto_compact_tokens=8_000,
            ),
            database_path=tmp_path / "compact.db",
            registry=registry,
            model=model,
        )
        try:
            events = [e async for e in runtime.stream("CURRENT INPUT VERBATIM")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert sum(isinstance(e, ContextCompacted) for e in events) == 1
            assert len(model.requests) == 3
            assert all(
                estimate_request_tokens(r.instructions, r.items, r.tools) < 17_100
                for r in model.requests
            )
            archived = await runtime._repository.load_items(runtime.thread_id)
            searches = [
                i
                for i in archived
                if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
            ]
            assert len(searches) == 1 and searches[0].discovered_tools == (tool.spec,)
            assert tool.calls == []
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
