import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import (
    ModelCompleted,
    ModelError,
    OpenAICompatibleModel,
    OpenAIResponsesModel,
    resolve_capabilities,
)
from corki.models.types import ModelUsage
from corki.protocol.events import ContextCompacted, TurnCompleted, TurnFailed
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("scenario_name", ["two_windows", "cold", "hard_cap"])
@pytest.mark.parametrize("transport", ["script", "chat_completions", "responses"])
def test_body_prefix_runtime_windows(tmp_path, scenario_name, transport):
    async def scenario():
        requests = []
        clients = []
        if scenario_name == "two_windows":
            usages = [
                (60_000, 100),
                (61_500, 100),
                (20, 0),
                (100_000, 100),
                (101_500, 100),
                (20, 0),
                (100, 10),
            ]
            expected = [(1, False), (2, False), (4, True), (5, False), (7, True)]
        elif scenario_name == "cold":
            usages = [(60_000, 100), (61_500, 100), (63_000, 100), (20, 0), (100, 10)]
            expected = [(1, False), (2, False), (3, False), (5, True)]
        else:
            usages = [(60_000, 100), (191_000, 100), (20, 0), (100, 10)]
            expected = [(1, False), (2, False), (4, True)]

        class Model:
            async def stream(self, request):
                requests.append(request)
                input_tokens, output_tokens = usages[len(requests) - 1]
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            "summary or final", request.items[-1].turn_id, new_step_id()
                        ),
                    ),
                    ModelUsage(input_tokens, output_tokens),
                )

            async def aclose(self):
                pass

        def create(thread=None):
            model = Model()
            if transport != "script":

                def respond(request):
                    requests.append(json.loads(request.content))
                    input_tokens, output_tokens = usages[len(requests) - 1]
                    if transport == "responses":
                        packet = {
                            "type": "response.completed",
                            "response": {
                                "id": f"response-{len(requests)}",
                                "usage": {
                                    "input_tokens": input_tokens,
                                    "output_tokens": output_tokens,
                                },
                                "output": [
                                    {
                                        "type": "message",
                                        "id": f"item-{len(requests)}",
                                        "role": "assistant",
                                        "content": [
                                            {"type": "output_text", "text": "summary or final"}
                                        ],
                                    }
                                ],
                            },
                        }
                    else:
                        packet = {
                            "usage": {
                                "prompt_tokens": input_tokens,
                                "completion_tokens": output_tokens,
                            },
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"content": "summary or final"},
                                    "finish_reason": "stop",
                                }
                            ],
                        }
                    return httpx.Response(200, text=f"data: {json.dumps(packet)}\n\n")

                client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
                clients.append(client)
                adapter = (
                    OpenAIResponsesModel if transport == "responses" else OpenAICompatibleModel
                )
                model = adapter(
                    api_key="fixture",
                    base_url="https://fixture.invalid/v1",
                    client=client,
                    capabilities=resolve_capabilities(
                        base_url="https://fixture.invalid/v1", api_mode=transport
                    ),
                )
            return LangGraphRuntime.create(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    skills_enabled=False,
                    context_window_tokens=200_000,
                    auto_compact_tokens=300_000 if scenario_name == "hard_cap" else 1000,
                    auto_compact_token_limit_scope="body_after_prefix",
                ),
                database_path=tmp_path / "sessions.db",
                thread_id=thread,
                registry=ToolRegistry(),
                model=model,
            )

        runtime = create()
        try:
            for index, (count, compacted) in enumerate(expected):
                if index == 1 and scenario_name == "cold":
                    thread = runtime.thread_id
                    await runtime.aclose()
                    runtime = create(thread)
                events = [event async for event in runtime.stream(f"user {index}")]
                assert isinstance(events[-1], TurnCompleted)
                assert any(isinstance(event, ContextCompacted) for event in events) == compacted
                assert len(requests) == count
        finally:
            await runtime.aclose()
            for client in clients:
                await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("tool_mode", ["direct", "code_mode_only"])
def test_tool_observation_growth_counts_against_body_budget(tmp_path, tool_mode):
    if tool_mode == "code_mode_only":
        from corki.code_mode.service import CodeModeService

        if not CodeModeService.available():
            pytest.skip("install corki[code-mode]")

    async def scenario():
        requests, calls = [], []

        class Echo:
            spec = ToolSpec("echo", "Fixture observation", {"type": "object"})

            async def execute(self, call, context):
                calls.append(call)
                return ToolResult(call.id, call.name, "x" * 8000)

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn = request.items[-1].turn_id
                if len(requests) == 1:
                    call = (
                        ToolCall(new_tool_call_id(), "echo", {})
                        if tool_mode == "direct"
                        else ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            raw_arguments="text(await tools.echo({}));",
                            input_kind="freeform",
                        )
                    )
                    yield ModelCompleted(
                        (ToolCallItem(call, turn, new_step_id()),), ModelUsage(60_000, 10)
                    )
                else:
                    yield ModelCompleted(
                        (AssistantMessageItem("summary or final", turn, new_step_id()),)
                    )

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Echo())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                context_window_tokens=200_000,
                auto_compact_tokens=1000,
                auto_compact_token_limit_scope="body_after_prefix",
                tool_mode=tool_mode,
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("run echo and use its observation")]
            assert isinstance(events[-1], TurnCompleted)
            assert sum(isinstance(event, ContextCompacted) for event in events) == 1
            assert len(requests) == 3 and len(calls) == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["manual_success", "manual_failure", "missing_usage"])
def test_manual_and_missing_usage_prefix_boundaries(tmp_path, mode):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                index = len(requests)
                if mode == "manual_failure" and index == 2:
                    raise ModelError("manual summary fixture failure")
                if mode == "missing_usage":
                    usage = ModelUsage()
                    content = "x" * 8000 if index == 1 else "summary or final"
                else:
                    content = "summary or final"
                    usage = {
                        1: ModelUsage(60_000, 1500 if mode == "manual_failure" else 100),
                        2: ModelUsage(199_000, 100),  # Summary usage must not become a body prefix.
                        3: ModelUsage(100_000, 100),
                        4: ModelUsage(101_500, 100),
                    }.get(index, ModelUsage(100, 10))
                yield ModelCompleted(
                    (AssistantMessageItem(content, request.items[-1].turn_id, new_step_id()),),
                    usage,
                )

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                context_window_tokens=200_000,
                auto_compact_tokens=1000,
                auto_compact_token_limit_scope="body_after_prefix",
                model_max_retries=0,
            ),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("first")]
            assert isinstance(events[-1], TurnCompleted)
            if mode != "missing_usage":
                events = [event async for event in runtime.compact()]
                assert isinstance(
                    events[-1], TurnFailed if mode == "manual_failure" else TurnCompleted
                )
                assert len(requests) == 2
            expected = (
                [(3, False), (4, False), (6, True)]
                if mode == "manual_success"
                else [(4 if mode == "manual_failure" else 3, True)]
            )
            for count, compacted in expected:
                events = [event async for event in runtime.stream("followup")]
                assert isinstance(events[-1], TurnCompleted)
                assert any(isinstance(event, ContextCompacted) for event in events) == compacted
                assert len(requests) == count
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
