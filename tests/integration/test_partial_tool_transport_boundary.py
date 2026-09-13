"""Item-complete Responses calls and uncommitted Chat deltas have distinct retry state."""

import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import OpenAICompatibleModel, OpenAIResponsesModel, resolve_capabilities
from corki.protocol.events import (
    AssistantMessageCompleted,
    AssistantTextDelta,
    ModelRetryScheduled,
    ProposedPlanCompleted,
    ProposedPlanDelta,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from corki.protocol.items import (
    AssistantMessageItem,
    ReasoningItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
)
from corki.protocol.tools import ToolResult, ToolSpec
from corki.tools import ToolRegistry


def packet(value):
    return ("data: " + json.dumps(value) + "\n\n").encode()


@pytest.mark.parametrize("api", ["responses", "chat_completions"])
@pytest.mark.parametrize("ending", ["eof", "read_error", "cancel"])
@pytest.mark.parametrize("projection", ["text", "open_plan", "closed_plan"])
def test_partial_text_without_terminal_cannot_complete_turn(tmp_path, api, ending, projection):
    async def scenario():
        requests, closed = [], []
        plan = projection != "text"
        text = "<proposed_plan>\n- unfinished\n" if plan else "unfinished"
        if projection == "closed_plan":
            text += "</proposed_plan>"

        class Broken(httpx.AsyncByteStream):
            async def __aiter__(self):
                if api == "responses":
                    yield packet(
                        {
                            "type": "response.output_text.delta",
                            "item_id": "partial",
                            "output_index": 0,
                            "content_index": 0,
                            "delta": text,
                        }
                    )
                else:
                    yield packet({"choices": [{"delta": {"content": text}}]})
                if ending == "read_error":
                    raise httpx.ReadError("partial text connection lost")
                if ending == "cancel":
                    raise asyncio.CancelledError

            async def aclose(self):
                closed.append(True)

        def handle(request):
            requests.append(request)
            return httpx.Response(200, stream=Broken())

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        cls = OpenAIResponsesModel if api == "responses" else OpenAICompatibleModel
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                model_max_retries=0,
                collaboration_mode="plan" if plan else "default",
            ),
            model=cls(
                api_key="fixture",
                base_url="https://fixture.invalid/v1",
                client=client,
                capabilities=resolve_capabilities(
                    base_url="https://fixture.invalid/v1",
                    api_mode=api,
                ),
            ),
            registry=ToolRegistry(),
            database_path=tmp_path / "session.db",
            home_path=tmp_path / "home",
        )
        events = []
        try:

            async def consume():
                async for event in runtime.stream("answer"):
                    events.append(event)

            if ending == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await consume()
            else:
                await consume()
            terminal = events[-1]
            delta_type = ProposedPlanDelta if plan else AssistantTextDelta
            assert "".join(e.delta for e in events if isinstance(e, delta_type)) == (
                "- unfinished\n" if plan else "unfinished"
            )
            assert not any(
                isinstance(e, (AssistantMessageCompleted, ProposedPlanCompleted)) for e in events
            )
            assert isinstance(terminal, TurnCancelled if ending == "cancel" else TurnFailed)
            assert (
                sum(isinstance(e, (TurnCompleted, TurnFailed, TurnCancelled)) for e in events) == 1
            )
            assert not any(isinstance(e, ModelRetryScheduled) for e in events)
            assert len(requests) == 1 and closed == [True]
            assert (
                await runtime._repository.load_model_step(terminal.thread_id, terminal.turn_id, 0)
                is None
            )
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("api", ["responses", "chat_completions"])
@pytest.mark.parametrize("ending", ["eof", "read_error"])
@pytest.mark.parametrize("mixed", [False, True])
def test_partial_call_retry_and_cold_history_never_repeat_side_effect(tmp_path, api, ending, mixed):
    async def scenario():
        bodies, executions, closed = [], [], []
        executed = asyncio.Event()
        response_call = {
            "type": "function_call",
            "id": "item-c",
            "call_id": "c",
            "name": "probe",
            "arguments": "{}",
        }
        chat_call = {
            "index": 0,
            "id": "c",
            "type": "function",
            "function": {"name": "probe", "arguments": "{}"},
        }

        class Probe:
            spec = ToolSpec("probe", "side effect fixture", {"type": "object"})

            async def execute(self, call, context):
                executions.append(call.id)
                executed.set()
                return ToolResult(call.id, call.name, "OBSERVED_ONCE")

        class Broken(httpx.AsyncByteStream):
            async def __aiter__(self):
                if api == "responses":
                    if mixed:
                        for item in (
                            {
                                "type": "reasoning",
                                "id": "r-first",
                                "summary": [{"type": "summary_text", "text": "Considering"}],
                            },
                            {
                                "type": "message",
                                "id": "m-first",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "Working"}],
                            },
                        ):
                            yield packet({"type": "response.output_item.done", "item": item})
                    yield packet({"type": "response.output_item.done", "item": response_call})
                    await asyncio.wait_for(executed.wait(), 2)
                else:
                    delta = {"tool_calls": [chat_call]}
                    if mixed:
                        delta["content"] = "Working"
                    yield packet({"choices": [{"delta": delta}]})
                    assert not executions
                if ending == "read_error":
                    raise httpx.ReadError("fixture partial response failure")

            async def aclose(self):
                closed.append(True)

        def handle(request):
            body = json.loads(request.content)
            bodies.append(body)
            if len(bodies) == 1:
                return httpx.Response(200, stream=Broken())
            key = "input" if api == "responses" else "messages"
            if api == "chat_completions" and len(bodies) == 2:
                assert not executions
                assert not any(item.get("role") == "tool" for item in body[key])
                return httpx.Response(
                    200,
                    content=packet(
                        {
                            "choices": [
                                {
                                    "delta": {"tool_calls": [chat_call]},
                                    "finish_reason": "tool_calls",
                                }
                            ]
                        }
                    )
                    + b"data: [DONE]\n\n",
                )
            if api == "responses":
                assert any(item.get("output") == "OBSERVED_ONCE" for item in body[key])
                return httpx.Response(
                    200,
                    content=packet(
                        {
                            "type": "response.completed",
                            "response": {"id": "done", "output": []},
                        }
                    ),
                )
            assert any(
                item.get("role") == "tool" and item.get("content") == "OBSERVED_ONCE"
                for item in body[key]
            )
            return httpx.Response(
                200,
                content=packet(
                    {
                        "choices": [
                            {
                                "delta": {"content": "done"},
                                "finish_reason": "stop",
                            }
                        ]
                    }
                )
                + b"data: [DONE]\n\n",
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))

        async def create(thread=None):
            registry = ToolRegistry()
            registry.register(Probe())
            cls = OpenAIResponsesModel if api == "responses" else OpenAICompatibleModel
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    skills_enabled=False,
                    model_max_retries=1,
                    model_retry_base_seconds=0.001,
                ),
                model=cls(
                    api_key="fixture",
                    base_url="https://fixture.invalid/v1",
                    client=client,
                    capabilities=resolve_capabilities(
                        base_url="https://fixture.invalid/v1",
                        api_mode=api,
                    ),
                ),
                registry=registry,
                database_path=tmp_path / "session.db",
                home_path=tmp_path / "home",
                thread_id=thread,
                load_plugins=False,
            )

        runtime = await create()
        try:
            events = [e async for e in runtime.stream("perform once")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(bodies) == (2 if api == "responses" else 3)
            assert len([e for e in events if isinstance(e, ModelRetryScheduled)]) == 1
            assert executions == ["c"] and closed == [True]
            terminal = events[-1]
            assert (
                await runtime._repository.load_model_step(terminal.thread_id, terminal.turn_id, 0)
                is None
            )
            partial = await runtime._repository.load_partial_step(
                terminal.thread_id, terminal.turn_id, 0
            )
            assert len(partial) == ((3 if mixed else 1) if api == "responses" else 0)
            history = await runtime._repository.load_items(runtime.thread_id)
            assert sum(isinstance(i, UserMessageItem) for i in history) == 1
            assert sum(isinstance(i, ToolCallItem) for i in history) == 1
            assert sum(isinstance(i, ToolResultItem) for i in history) == 1
            assert sum(isinstance(i, ReasoningItem) for i in history) == int(
                mixed and api == "responses"
            )
            assert sum(
                isinstance(i, AssistantMessageItem) and i.content == "Working" for i in history
            ) == int(mixed and api == "responses")
            prior = bodies[-1]
            thread = runtime.thread_id
            await runtime.aclose()
            runtime = await create(thread)
            assert isinstance([e async for e in runtime.stream("continue")][-1], TurnCompleted)
            key = "input" if api == "responses" else "messages"
            assert bodies[-1][key][: len(prior[key])] == prior[key]
            assert (await runtime._repository.load_items(thread))[: len(history)] == history
            assert executions == ["c"]
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())
