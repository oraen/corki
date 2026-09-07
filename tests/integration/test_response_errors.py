"""Codex Responses SSE error semantics exercised through the real Runtime."""

import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import OpenAIResponsesModel, resolve_capabilities
from corki.protocol.events import ModelRetryScheduled, TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.items import ToolResultItem
from corki.protocol.tools import ToolResult, ToolSpec
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolRegistry


def packet(kind, **fields):
    return "data: " + json.dumps({"type": kind, **fields}) + "\n\n"


@pytest.mark.parametrize(
    "code,kind,retry",
    [
        ("context_length_exceeded", "context_window", False),
        ("insufficient_quota", "quota", False),
        ("usage_not_included", "usage_not_included", False),
        ("cyber_policy", "cyber_policy", False),
        ("misalignment_policy_violation", "misalignment_policy", False),
        ("invalid_prompt", "invalid_request", False),
        ("bio_policy", "invalid_request", False),
        ("server_is_overloaded", "server_overloaded", False),
        ("slow_down", "server_overloaded", False),
        ("rate_limit_exceeded", "rate_limit", True),
        ("unknown", "server", True),
        (None, "server", True),
    ],
)
def test_failed_code_controls_actual_runtime_retry_and_observation(tmp_path, code, kind, retry):
    async def scenario():
        requests, calls = [], []

        class Tool:
            spec = ToolSpec("probe", "fixture", {"type": "object"})

            async def execute(self, call, context):
                calls.append(call.id)
                return ToolResult(call.id, call.name, "observed")

        def handle(request):
            requests.append(json.loads(request.content))
            body = ""
            if len(requests) == 1:
                body += packet(
                    "response.output_item.done",
                    item={
                        "type": "function_call",
                        "id": "i",
                        "call_id": "c",
                        "name": "probe",
                        "arguments": "{}",
                    },
                )
            else:
                assert any(item.get("output") == "observed" for item in requests[-1]["input"])
            body += packet(
                "response.failed",
                response={"error": {"code": code, "message": "fixture rejection"}},
            )
            return httpx.Response(200, text=body)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        model = OpenAIResponsesModel(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            client=client,
            capabilities=resolve_capabilities(
                base_url="https://fixture.invalid/v1", api_mode="responses"
            ),
        )
        registry = ToolRegistry()
        registry.register(Tool())
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                model_max_retries=1,
                model_retry_base_seconds=0.001,
            ),
            database_path=repository.path,
            repository=repository,
            registry=registry,
            model=model,
        )
        try:
            events = [event async for event in runtime.stream("run")]
            assert isinstance(events[-1], TurnFailed), events[-1]
            assert (events[-1].error_kind, events[-1].retryable) == (kind, retry)
            assert len(requests) == (2 if retry else 1)
            assert calls == ["c"]
            history = await repository.load_items(events[-1].thread_id)
            assert len([item for item in history if isinstance(item, ToolResultItem)]) == 1
            failure = await repository.load_model_failure(
                events[-1].thread_id, events[-1].turn_id, 0
            )
            assert (failure.kind, failure.retryable) == (kind, retry)
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("ending", ["eof", "completed", "cancel"])
def test_pending_error_keeps_later_tool_items_and_owns_stream_until_terminal(tmp_path, ending):
    async def scenario():
        executed, waiting = asyncio.Event(), asyncio.Event()
        calls = []

        class Tool:
            spec = ToolSpec("probe", "fixture", {"type": "object"})

            async def execute(self, call, context):
                calls.append(call.id)
                executed.set()
                return ToolResult(call.id, call.name, "observed")

        class Stream(httpx.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                yield packet(
                    "response.failed", response={"error": {"code": "insufficient_quota"}}
                ).encode()
                yield packet(
                    "response.output_item.done",
                    item={
                        "type": "function_call",
                        "id": "i",
                        "call_id": "c",
                        "name": "probe",
                        "arguments": "{}",
                    },
                ).encode()
                await asyncio.wait_for(executed.wait(), 2)
                waiting.set()
                if ending == "completed":
                    yield packet("response.completed", response={"id": "done"}).encode()
                elif ending == "cancel":
                    await asyncio.Event().wait()

            async def aclose(self):
                self.closed = True

        stream = Stream()
        requests = []

        def handle(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(200, stream=stream)
            return httpx.Response(200, text=packet("response.completed", response={"id": "final"}))

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        model = OpenAIResponsesModel(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            client=client,
            capabilities=resolve_capabilities(
                base_url="https://fixture.invalid/v1", api_mode="responses"
            ),
        )
        registry = ToolRegistry()
        registry.register(Tool())
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=repository.path,
            repository=repository,
            registry=registry,
            model=model,
        )
        events = []

        async def consume():
            try:
                async for event in runtime.stream("run"):
                    events.append(event)
            except asyncio.CancelledError:
                assert ending == "cancel"

        task = asyncio.create_task(consume())
        try:
            if ending == "cancel":
                await asyncio.wait_for(waiting.wait(), 2)
                await runtime.cancel_active()
            await asyncio.wait_for(task, 3)
            expected = {"eof": TurnFailed, "completed": TurnCompleted, "cancel": TurnCancelled}[
                ending
            ]
            assert isinstance(events[-1], expected), events[-1]
            assert calls == ["c"]
            assert stream.closed
            assert len(requests) == (2 if ending == "completed" else 1)
            failure = await repository.load_model_failure(
                events[-1].thread_id, events[-1].turn_id, 0
            )
            assert (failure.kind if failure else None) == ("quota" if ending == "eof" else None)
            assert not any(isinstance(event, ModelRetryScheduled) for event in events)
        finally:
            await runtime.aclose()
            await asyncio.gather(task, return_exceptions=True)
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "message,delay",
    [
        ("Try again in 11.054s", 11.054),
        ("try again in 125.9ms", 0.125),
        ("TRY AGAIN IN 2 seconds", 2),
    ],
)
def test_sse_rate_delay_reaches_runtime_retry_event(tmp_path, monkeypatch, message, delay):
    async def scenario():
        delays, requests = [], []

        async def fast_wait(value, realtime):
            delays.append(value)
            await asyncio.sleep(0)

        monkeypatch.setattr("corki.core.graph.wait_retry", fast_wait)

        def handle(request):
            requests.append(request)
            if len(requests) == 1:
                body = packet(
                    "response.failed",
                    response={"error": {"code": "rate_limit_exceeded", "message": message}},
                )
            else:
                body = packet("response.completed", response={"id": "done"})
            return httpx.Response(200, text=body, headers={"Retry-After": "99"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        model = OpenAIResponsesModel(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            client=client,
            capabilities=resolve_capabilities(
                base_url="https://fixture.invalid/v1", api_mode="responses"
            ),
        )
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "sessions.db",
            model=model,
        )
        try:
            events = [event async for event in runtime.stream("run")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert delays == [delay]
            assert [
                event.delay_seconds for event in events if isinstance(event, ModelRetryScheduled)
            ] == [delay]
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())
