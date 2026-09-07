"""Retries rebuild from durable completed output, not the stale request body."""

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import (
    ModelCompleted,
    ModelError,
    ModelErrorKind,
    ModelItemCompleted,
    OpenAIResponsesModel,
    resolve_capabilities,
)
from corki.protocol.events import ModelRetryScheduled, TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.ids import ToolCallId
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolExposure, ToolResult, ToolSpec
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolRegistry


@pytest.mark.parametrize("failures", [1, 4])
def test_retry_after_tool_output_uses_updated_history_and_bounded_budget(tmp_path, failures):
    async def scenario():
        requests, executions = [], []
        executed = asyncio.Event()

        class Tool:
            spec = ToolSpec("probe", "fixture", {"type": "object"})

            async def execute(self, call, context):
                executions.append(call.id)
                executed.set()
                return ToolResult(call.id, call.name, "observed")

        class Stream(httpx.AsyncByteStream):
            def __init__(self, first):
                self.first = first

            async def __aiter__(self):
                if self.first:
                    yield (
                        "data: "
                        + json.dumps(
                            {
                                "type": "response.output_item.done",
                                "item": {
                                    "type": "function_call",
                                    "id": "i",
                                    "call_id": "c",
                                    "name": "probe",
                                    "arguments": "{}",
                                },
                            }
                        )
                        + "\n\n"
                    ).encode()
                    await asyncio.wait_for(executed.wait(), 1)
                raise httpx.ReadError("fixture disconnect")

        async def handle(request):
            requests.append(json.loads(request.content))
            if len(requests) > 1:
                assert any(item.get("output") == "observed" for item in requests[-1]["input"])
            if len(requests) <= failures:
                return httpx.Response(200, stream=Stream(len(requests) == 1))
            return httpx.Response(
                200,
                text="data: "
                + json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "final",
                            "output": [
                                {
                                    "type": "message",
                                    "id": "m",
                                    "content": [{"type": "output_text", "text": "done"}],
                                }
                            ],
                        },
                    }
                )
                + "\n\n",
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        model = OpenAIResponsesModel(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            client=client,
            max_retries=7,
            retry_base_seconds=0.001,
            capabilities=resolve_capabilities(
                base_url="https://fixture.invalid/v1", api_mode="responses"
            ),
        )
        registry = ToolRegistry()
        registry.register(Tool())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                model_max_retries=2,
                model_retry_base_seconds=0.001,
                max_steps=2,
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=model,
        )
        try:
            events = [event async for event in runtime.stream("run")]
            assert isinstance(events[-1], TurnCompleted if failures == 1 else TurnFailed), events[
                -1
            ]
            assert len(requests) == (2 if failures == 1 else 3)
            assert executions == ["c"]
            retries = [event for event in events if isinstance(event, ModelRetryScheduled)]
            assert [event.attempt for event in retries] == ([1] if failures == 1 else [1, 2])
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


def test_many_retries_do_not_consume_logical_step_or_graph_recursion_budget(tmp_path):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) <= 20:
                    raise ModelError(
                        "retry",
                        kind=ModelErrorKind.TRANSPORT,
                        retryable=True,
                        retry_after_seconds=0,
                    )
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, max_steps=1, model_max_retries=20
            ),
            database_path=tmp_path / "sessions.db",
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("run")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 21
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["discovery", "unknown"])
def test_retry_preserves_discovered_definitions_and_unknown_side_effects(tmp_path, mode):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        requests, executions = [], []

        class Tool:
            spec = ToolSpec(
                "calendar",
                "calendar appointment scheduler",
                {"type": "object"},
                exposure=ToolExposure.DEFERRED if mode == "discovery" else ToolExposure.DIRECT,
            )

            async def execute(self, call, context):
                executions.append(call)
                return ToolResult(call.id, call.name, "scheduled")

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    if mode == "discovery":
                        assert "calendar" not in {spec.name for spec in request.tools}
                        call = ToolCall(
                            ToolCallId("search"), "tool_search", {"query": "calendar appointment"}
                        )
                    else:
                        call = ToolCall(ToolCallId("unknown"), "calendar", {})
                        await repository.claim_tool_call(runtime.thread_id, turn, call)
                    yield ModelItemCompleted(ToolCallItem(call, turn, step))
                    raise ModelError("lost stream", kind=ModelErrorKind.TRANSPORT, retryable=True)
                if mode == "discovery" and len(requests) == 2:
                    assert "calendar" in {spec.name for spec in request.tools}
                    assert any(
                        isinstance(item, ToolResultItem) and item.discovered_tools
                        for item in request.items
                    )
                    yield ModelCompleted(
                        (ToolCallItem(ToolCall(ToolCallId("book"), "calendar", {}), turn, step),)
                    )
                else:
                    results = [item for item in request.items if isinstance(item, ToolResultItem)]
                    if mode == "unknown":
                        assert (
                            len(results) == 1
                            and results[0].is_error
                            and "unknown" in results[0].content
                        )
                    else:
                        assert results[-1].content == "scheduled"
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Tool())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, model_retry_base_seconds=0.001
            ),
            database_path=repository.path,
            repository=repository,
            registry=registry,
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("run")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(executions) == (1 if mode == "discovery" else 0)
            assert len(requests) == (3 if mode == "discovery" else 2)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("crash_retry", [0, 1])
def test_failure_commit_crash_preserves_history_and_retry_budget(tmp_path, crash_retry):
    async def scenario():
        fixture = Path(__file__).parents[1] / "fixtures" / "retry_crash.py"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(fixture),
            str(tmp_path),
            str(crash_retry),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 8)
            assert process.returncode == 23, (stdout, stderr)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        thread = await repository.latest_thread(tmp_path)
        turn = await repository.latest_running_turn(thread)
        assert turn is not None
        requests = []

        class Tool:
            spec = ToolSpec("probe", "fixture", {"type": "object"})

            async def execute(self, call, context):
                raise AssertionError("must not replay side effect")

        class Model:
            async def stream(self, request):
                requests.append(request)
                assert any(
                    isinstance(item, ToolResultItem) and item.content == "observed"
                    for item in request.items
                )
                yield ModelCompleted((AssistantMessageItem("done", turn.id, new_step_id()),))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Tool())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                model_max_retries=1,
                model_retry_base_seconds=0.001,
            ),
            database_path=repository.path,
            repository=repository,
            thread_id=thread,
            registry=registry,
            model=Model(),
        )
        try:
            events = [event async for event in runtime.resume_pending()]
            assert isinstance(events[-1], TurnCompleted if crash_retry == 0 else TurnFailed), (
                events[-1]
            )
            assert len(requests) == (1 if crash_retry == 0 else 0)
            assert (tmp_path / "side-effect.txt").read_text() == "executed\n"
            failure = await repository.load_model_failure(thread, turn.id, crash_retry)
            assert failure.retries_used == crash_retry
            assert await repository.load_model_step(thread, turn.id, crash_retry) is None
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status,body,retries",
    [
        (401, "unauthorized", 1),
        (413, "context length exceeded", 1),
        (503, "unavailable", 1),
        (200, "data: {broken\n\n", 1),
        (200, "data: []\n\n", 1),
        (200, "", 1),
    ],
)
def test_runtime_retry_classification_and_logical_step_budget(tmp_path, status, body, retries):
    async def scenario():
        requests = []

        async def handle(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(status, text=body)
            return httpx.Response(
                200, text='data: {"type":"response.completed","response":{"id":"r"}}\n\n'
            )

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
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                max_steps=1,
                model_max_retries=1,
                model_retry_base_seconds=0.001,
            ),
            database_path=tmp_path / "sessions.db",
            model=model,
        )
        try:
            events = [event async for event in runtime.stream("run")]
            assert isinstance(events[-1], TurnCompleted if retries else TurnFailed), events[-1]
            assert len(requests) == retries + 1
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["cancel", "steer"])
@pytest.mark.parametrize("kind", [ModelErrorKind.TRANSPORT, ModelErrorKind.CONNECTION])
def test_backoff_is_owned_and_interruptible_by_user(tmp_path, action, kind):
    async def scenario():
        backoff = asyncio.Event()
        calls = []

        class Model:
            async def stream(self, request):
                calls.append(request)
                if len(calls) == 1:
                    raise ModelError(
                        "retry later",
                        kind=kind,
                        retryable=True,
                        retry_after_seconds=60,
                    )
                assert request.items[-1].content == "new input"
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "sessions.db",
            model=Model(),
        )

        async def collect():
            events = []
            try:
                async for event in runtime.stream("run", realtime=True):
                    events.append(event)
                    if isinstance(event, ModelRetryScheduled):
                        assert event.delay_seconds == (
                            5 if kind == ModelErrorKind.CONNECTION else 60
                        )
                        backoff.set()
            except asyncio.CancelledError:
                assert action == "cancel"
            return events

        task = asyncio.create_task(collect())
        try:
            await asyncio.wait_for(backoff.wait(), 2)
            if action == "cancel":
                await runtime.cancel_active()
            else:
                await runtime.steer("new input")
            events = await asyncio.wait_for(task, 2)
            assert isinstance(events[-1], TurnCancelled if action == "cancel" else TurnCompleted)
            assert len(calls) == (1 if action == "cancel" else 2)
            assert not any(
                task.get_name().startswith("corki-retry-") for task in asyncio.all_tasks()
            )
        finally:
            await runtime.aclose()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
