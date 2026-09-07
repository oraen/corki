import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import (
    ModelCompleted,
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


@pytest.mark.parametrize("path", ["continuation", "tool", "tool_tail", "cross_turn", "cold"])
@pytest.mark.parametrize("empty", [False, True])
def test_provider_usage_drives_runtime_compaction(tmp_path, path, empty):
    async def scenario():
        requests = []

        class Echo:
            spec = ToolSpec("echo", "Return a small observation", {"type": "object"})

            async def execute(self, call, context):
                return ToolResult(
                    call.id, call.name, "x" * 8000 if path == "tool_tail" else "observation"
                )

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn = request.items[-1].turn_id
                if len(requests) == 2:
                    step = new_step_id()
                    items = () if empty else (AssistantMessageItem("first", turn, step),)
                    if path in {"tool", "tool_tail"}:
                        items = (
                            *items,
                            ToolCallItem(ToolCall(new_tool_call_id(), "echo", {}), turn, step),
                        )
                    yield ModelCompleted(
                        items,
                        usage=ModelUsage(
                            input_tokens=28_500 if path == "tool_tail" else 30_500,
                            output_tokens=500,
                        ),
                        end_turn=False if path == "continuation" else None,
                    )
                else:
                    yield ModelCompleted(
                        (AssistantMessageItem("summary or final", turn, new_step_id()),)
                    )

            async def aclose(self):
                pass

        def create(thread=None):
            registry = ToolRegistry()
            registry.register(Echo())
            return LangGraphRuntime.create(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    skills_enabled=False,
                    context_window_tokens=40_000,
                    auto_compact_tokens=30_000,
                ),
                database_path=tmp_path / "sessions.db",
                thread_id=thread,
                registry=registry,
                model=Model(),
            )

        runtime = create()
        try:
            [event async for event in runtime.stream("earlier history")]
            events = [event async for event in runtime.stream("first request")]
            if path in {"cross_turn", "cold"}:
                if path == "cold":
                    thread = runtime.thread_id
                    await runtime.aclose()
                    runtime = create(thread)
                events += [event async for event in runtime.stream("next request")]
            assert isinstance(events[-1], TurnCompleted)
            assert sum(isinstance(event, ContextCompacted) for event in events) == 1
            assert len(requests) == 4
            # The replacement invalidates old usage, including after reopening.
            events = [event async for event in runtime.stream("small followup")]
            assert isinstance(events[-1], TurnCompleted)
            assert not any(isinstance(event, ContextCompacted) for event in events)
            assert len(requests) == 5
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("reported", [False, True])
def test_low_usage_overrides_local_overestimate_but_absence_keeps_fallback(tmp_path, reported):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                first = len(requests) == 1
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            "x" * 100_000 if first else "small",
                            request.items[-1].turn_id,
                            new_step_id(),
                        ),
                    ),
                    ModelUsage(input_tokens=100) if first and reported else ModelUsage(),
                )

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                context_window_tokens=80_000,
                auto_compact_tokens=20_000,
            ),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=Model(),
        )
        try:
            first = [event async for event in runtime.stream("first")]
            assert isinstance(first[-1], TurnCompleted)
            events = [event async for event in runtime.stream("second")]
            assert isinstance(events[-1], TurnCompleted)
            assert sum(isinstance(event, ContextCompacted) for event in events) == (not reported)
            assert len(requests) == (2 if reported else 3)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_empty_completion_cannot_bypass_reported_hard_context_limit(tmp_path):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted((), ModelUsage(input_tokens=50_000), end_turn=False)

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, context_window_tokens=40_000
            ),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("keep this request intact")]
            assert isinstance(events[-1], TurnFailed)
            assert events[-1].error_kind == "context_window"
            assert len(requests) == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("kind", ["high", "low", "missing", "invalid"])
def test_http_usage_reaches_real_runtime_window(tmp_path, mode, kind):
    async def scenario():
        payloads = []

        def respond(request):
            payloads.append(json.loads(request.content))
            usage = {}
            if len(payloads) == 1 and kind != "missing":
                usage = {
                    "total_tokens": {"high": 31_000, "low": 100, "invalid": True}[kind],
                    "input_tokens" if mode == "responses" else "prompt_tokens": 30_999,
                    "output_tokens" if mode == "responses" else "completion_tokens": 999,
                }
            if mode == "responses":
                packets = [
                    {
                        "type": "response.completed",
                        "response": {
                            "id": f"response-{len(payloads)}",
                            "usage": usage,
                            "output": [
                                {
                                    "type": "message",
                                    "id": f"item-{len(payloads)}",
                                    "role": "assistant",
                                    "content": [
                                        {"type": "output_text", "text": "summary or final"}
                                    ],
                                }
                            ],
                        },
                    }
                ]
            else:
                packets = [
                    {
                        "usage": usage,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": "summary or final"},
                                "finish_reason": "stop",
                            }
                        ],
                    }
                ]
            body = "".join(f"data: {json.dumps(packet)}\n\n" for packet in packets)
            return httpx.Response(
                200,
                text=body,
                headers=({"x-reasoning-included": "false"} if len(payloads) == 1 else {}),
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        adapter = OpenAIResponsesModel if mode == "responses" else OpenAICompatibleModel
        model = adapter(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            client=client,
            capabilities=resolve_capabilities(base_url="https://fixture.invalid/v1", api_mode=mode),
        )
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                context_window_tokens=40_000,
                auto_compact_tokens=30_000,
                model_max_retries=0,
            ),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=model,
        )
        try:
            events = [event async for event in runtime.stream("first")]
            if kind == "invalid":
                assert isinstance(events[-1], TurnFailed)
                assert await runtime._repository.load_context_usage(runtime.thread_id) is None
                return
            assert isinstance(events[-1], TurnCompleted)
            usage = await runtime._repository.load_context_usage(runtime.thread_id)
            if kind == "missing":
                assert usage is None
            else:
                assert usage.total_tokens == (31_000 if kind == "high" else 100)
                assert usage.server_reasoning_included is (mode == "responses")
            events = [event async for event in runtime.stream("followup")]
            assert isinstance(events[-1], TurnCompleted)
            assert sum(isinstance(event, ContextCompacted) for event in events) == (kind == "high")
            assert len(payloads) == (3 if kind == "high" else 2)
            if mode == "responses":
                # Header presence, not its textual value, enables the sticky capability.
                assert model._server_reasoning_included
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())
