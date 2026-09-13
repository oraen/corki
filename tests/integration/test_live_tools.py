"""Complete SSE items must execute before the provider completes its response."""

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import (
    ModelCompleted,
    ModelItemCompleted,
    OpenAIResponsesModel,
    resolve_capabilities,
)
from corki.protocol.events import ToolCallCompleted, TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.ids import ToolCallId, new_thread_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolConcurrency, ToolResult, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.storage import SQLiteSessionRepository
from corki.tools import FatalToolError, ToolRegistry


def sse(value):
    return f"data: {json.dumps(value)}\n\n".encode()


@pytest.mark.parametrize("truncate", [False, True])
@pytest.mark.parametrize("input_kind", ["json", "freeform"])
def test_responses_executes_complete_call_before_stream_terminal(tmp_path, truncate, input_kind):
    async def scenario():
        executed = asyncio.Event()
        finish_tool = asyncio.Event()
        calls = []
        requests = []
        closed = []
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")

        class Tool:
            spec = ToolSpec("probe", "stream fixture", {"type": "object"}, input_kind=input_kind)

            async def execute(self, call, context):
                assert call.input_kind == input_kind
                if input_kind == "freeform":
                    assert call.raw_arguments == "text('raw')" and call.arguments is None
                history = await repository.load_items(runtime.thread_id)
                assert any(isinstance(item, ToolCallItem) for item in history)
                calls.append(call.id)
                executed.set()
                if truncate:
                    await finish_tool.wait()
                return ToolResult(call.id, call.name, "observed")

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield sse(
                    {
                        "type": "response.output_item.done",
                        "item": {
                            "type": "function_call",
                            "id": "item-1",
                            "call_id": "call-1",
                            "name": "probe",
                            "arguments": json.dumps({"input": "text('raw')"})
                            if input_kind == "freeform"
                            else "{}",
                        },
                    }
                )
                await asyncio.wait_for(executed.wait(), 1)
                if truncate:
                    raise httpx.ReadError("fixture disconnected after execution")
                yield sse({"type": "response.completed", "response": {"id": "r1"}})

            async def aclose(self):
                closed.append(True)
                # A transport failure does not cancel the tool. Its eventual
                # result must still be drained into history before TurnFailed.
                asyncio.get_running_loop().call_later(0.05, finish_tool.set)

        async def handle(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            requests.append(json.loads(request.content))
            assert all(tool["type"] == "function" for tool in requests[-1]["tools"])
            if len(requests) == 1:
                return httpx.Response(200, stream=Stream())
            return httpx.Response(
                200,
                content=sse(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "r2",
                            "output": [
                                {
                                    "type": "message",
                                    "content": [{"type": "output_text", "text": "done"}],
                                }
                            ],
                        },
                    }
                ),
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        model = OpenAIResponsesModel(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            capabilities=replace(
                resolve_capabilities(base_url="https://fixture.invalid/v1", api_mode="responses"),
                supports_native_freeform=True,
            ),
            client=client,
            retry_base_seconds=0,
        )
        registry = ToolRegistry()
        registry.register(Tool())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                model_max_retries=0,
                api_mode="responses",
                tool_freeform_mode="native",
            ),
            database_path=repository.path,
            repository=repository,
            registry=registry,
            model=model,
        )
        try:
            events = [event async for event in runtime.stream("probe now")]
            assert calls == ["call-1"]
            assert len(closed) == 1
            history = await repository.load_items(runtime.thread_id)
            results = [item for item in history if isinstance(item, ToolResultItem)]
            assert len(results) == 1 and results[0].content == "observed"
            assert results[0].input_kind == input_kind
            if truncate:
                assert isinstance(events[-1], TurnFailed), events[-1]
                assert len(requests) == 1  # never retry the stale request after side effects
            else:
                assert isinstance(events[-1], TurnCompleted), events[-1]
                assert len(requests) == 2
                assert sum(isinstance(event, ToolCallCompleted) for event in events) == 1
                assert any(item.get("output") == "observed" for item in requests[1]["input"])
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("limit", ["steps", "tools"])
def test_streamed_call_is_budget_checked_before_dispatch(tmp_path, limit):
    async def scenario():
        calls = []

        class Tool:
            spec = ToolSpec("probe", "fixture", {"type": "object"})

            async def execute(self, call, context):
                calls.append(call)
                return ToolResult(call.id, call.name, "ran")

        class Model:
            async def stream(self, request):
                turn, step = request.items[-1].turn_id, new_step_id()
                items = tuple(
                    ToolCallItem(ToolCall(ToolCallId(str(i)), "probe", {}), turn, step)
                    for i in range(2)
                )
                for item in items:
                    yield ModelItemCompleted(item)
                yield ModelCompleted(items)

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Tool())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                max_steps=1 if limit == "steps" else 8,
                max_tool_calls=1,
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("run")]
            assert isinstance(events[-1], TurnFailed), events[-1]
            assert "limit exceeded" in events[-1].error
            assert len(calls) <= (0 if limit == "steps" else 1)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_steering_drains_live_results_before_accepting_input(tmp_path):
    async def scenario():
        started, release, model_closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        release_model = asyncio.Event()
        requests, executed = [], []
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")

        class Tool:
            spec = ToolSpec("probe", "fixture", {"type": "object"})

            async def execute(self, call, context):
                started.set()
                await release.wait()
                executed.append(call)
                return ToolResult(call.id, call.name, "observed")

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    try:
                        item = ToolCallItem(
                            ToolCall(ToolCallId("steered"), "probe", {}), turn, step
                        )
                        yield ModelItemCompleted(item)
                        await release_model.wait()
                        yield ModelCompleted((item,))
                    finally:
                        model_closed.set()
                else:
                    assert request.items[-1].content == "new input"
                    assert any(
                        isinstance(item, ToolResultItem) and item.content == "observed"
                        for item in request.items
                    )
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Tool())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=repository.path,
            repository=repository,
            registry=registry,
            model=Model(),
        )

        async def collect():
            return [event async for event in runtime.stream("run", realtime=True)]

        task = asyncio.create_task(collect())
        try:
            await asyncio.wait_for(started.wait(), 2)
            await runtime.steer("new input")
            await asyncio.sleep(0.02)
            assert not model_closed.is_set() and len(requests) == 1
            release_model.set()
            await asyncio.wait_for(model_closed.wait(), 2)
            history = await repository.load_items(runtime.thread_id)
            assert not any(
                isinstance(item, UserMessageItem) and item.content == "new input"
                for item in history
            )
            release.set()
            events = await asyncio.wait_for(task, 3)
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(executed) == 1 and len(requests) == 2
            history = await repository.load_items(runtime.thread_id)
            result_index = next(
                i for i, item in enumerate(history) if isinstance(item, ToolResultItem)
            )
            input_index = next(
                i
                for i, item in enumerate(history)
                if isinstance(item, UserMessageItem) and item.content == "new input"
            )
            assert result_index < input_index
        finally:
            release_model.set()
            release.set()
            await runtime.aclose()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_live_parallel_groups_obey_exclusive_barrier_and_result_order(tmp_path):
    async def scenario():
        started = {str(i): asyncio.Event() for i in range(4)}
        finished = []
        model_calls = []

        class Tool:
            def __init__(self, name, parallel):
                self.spec = ToolSpec(
                    name,
                    "fixture",
                    {"type": "object"},
                    concurrency=(
                        ToolConcurrency.PARALLEL if parallel else ToolConcurrency.EXCLUSIVE
                    ),
                )

            async def execute(self, call, context):
                key = str(call.id)
                started[key].set()
                if key in {"0", "1"}:
                    await asyncio.wait_for(started["1" if key == "0" else "0"].wait(), 1)
                elif key == "2":
                    assert set(finished) == {"0", "1"}
                else:
                    assert set(finished) == {"0", "1", "2"}
                finished.append(key)
                return ToolResult(call.id, call.name, key)

        class Model:
            async def stream(self, request):
                model_calls.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(model_calls) == 1:
                    items = tuple(
                        ToolCallItem(ToolCall(ToolCallId(str(i)), name, {}), turn, step)
                        for i, name in enumerate(("parallel", "parallel", "exclusive", "parallel"))
                    )
                    for item in items:
                        yield ModelItemCompleted(item)
                    await asyncio.wait_for(started["3"].wait(), 1)
                    yield ModelCompleted(items)
                else:
                    results = [
                        item.content for item in request.items if isinstance(item, ToolResultItem)
                    ]
                    assert results == ["0", "1", "2", "3"]
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Tool("parallel", True))
        registry.register(Tool("exclusive", False))
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("run")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(model_calls) == 2 and len(finished) == 4
            assert sum(isinstance(event, ToolCallCompleted) for event in events) == 4
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("fatal", [False, True])
@pytest.mark.parametrize("input_kind", ["json", "freeform"])
def test_live_failure_or_cancel_closes_hung_model_and_tools(tmp_path, fatal, input_kind):
    async def scenario():
        started = asyncio.Event()
        tool_closed = asyncio.Event()
        model_closed = asyncio.Event()
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")

        class Tool:
            spec = ToolSpec(
                "probe",
                "fixture",
                {"type": "object"},
                concurrency=ToolConcurrency.PARALLEL,
                input_kind=input_kind,
            )

            async def execute(self, call, context):
                if call.id == "fatal":
                    await started.wait()
                    raise FatalToolError("fixture fatal")
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    tool_closed.set()

        class Model:
            async def stream(self, request):
                try:
                    turn, step = request.items[-1].turn_id, new_step_id()
                    yield ModelItemCompleted(
                        ToolCallItem(
                            ToolCall(
                                ToolCallId("hung"),
                                "probe",
                                None if input_kind == "freeform" else {},
                                input_kind=input_kind,
                            ),
                            turn,
                            step,
                        )
                    )
                    if fatal:
                        yield ModelItemCompleted(
                            ToolCallItem(
                                ToolCall(
                                    ToolCallId("fatal"),
                                    "probe",
                                    None if input_kind == "freeform" else {},
                                    input_kind=input_kind,
                                ),
                                turn,
                                step,
                            )
                        )
                    await asyncio.Event().wait()
                finally:
                    model_closed.set()

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Tool())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=repository.path,
            repository=repository,
            registry=registry,
            model=Model(),
        )

        async def collect():
            events = []
            try:
                async for event in runtime.stream("run"):
                    events.append(event)
            except asyncio.CancelledError:
                assert not fatal
            return events

        task = asyncio.create_task(collect())
        try:
            await asyncio.wait_for(started.wait(), 2)
            if not fatal:
                await runtime.cancel_active()
            events = await asyncio.wait_for(task, 2)
            assert isinstance(events[-1], TurnFailed if fatal else TurnCancelled), events[-1]
            assert tool_closed.is_set() and model_closed.is_set()
            history = await repository.load_items(runtime.thread_id)
            results = [item for item in history if isinstance(item, ToolResultItem)]
            assert len(results) == (2 if fatal else 1)
            assert all(item.is_error and "unknown" in item.content for item in results)
            assert all(item.input_kind == input_kind for item in results)
        finally:
            await runtime.aclose()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("claim_state", ["unclaimed", "running", "completed"])
def test_partial_response_resume_does_not_resample_old_request(tmp_path, claim_state):
    async def scenario():
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        thread, turn = new_thread_id(), new_turn_id()
        await repository.create_thread(thread, tmp_path)
        await repository.save_turn(TurnRecord(turn, thread, TurnStatus.RUNNING, "run"))
        await repository.append_items(thread, (UserMessageItem("run", turn),))
        call = ToolCall(ToolCallId("durable"), "probe", {})
        item = ToolCallItem(call, turn, new_step_id())
        await repository.append_partial_item(thread, turn, 0, item)
        if claim_state != "unclaimed":
            await repository.claim_tool_call(thread, turn, call)
        if claim_state == "completed":
            await repository.complete_tool_call(
                thread, turn, ToolResult(call.id, call.name, "saved")
            )
        repository = SQLiteSessionRepository(repository.path)
        calls = []
        requests = []

        class Tool:
            spec = ToolSpec("probe", "fixture", {"type": "object"})

            async def execute(self, call, context):
                calls.append(call)
                return ToolResult(call.id, call.name, "new")

        class Model:
            async def stream(self, request):
                requests.append(request)
                results = [item for item in request.items if isinstance(item, ToolResultItem)]
                assert len(results) == 1
                if claim_state == "running":
                    assert results[0].is_error and "unknown" in results[0].content
                else:
                    assert results[0].content == ("new" if claim_state == "unclaimed" else "saved")
                yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Tool())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=repository.path,
            repository=repository,
            thread_id=thread,
            registry=registry,
            model=Model(),
        )
        try:
            events = [event async for event in runtime.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 1 and len(calls) == (claim_state == "unclaimed")
            assert await repository.load_model_step(thread, turn, 0) is None
            assert await repository.load_partial_step(thread, turn, 0) == (item,)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["unclaimed", "running", "completed"])
@pytest.mark.parametrize("input_kind", ["json", "freeform"])
def test_real_process_exit_resumes_partial_step_with_checkpoint(tmp_path, phase, input_kind):
    async def scenario():
        fixture = Path(__file__).parents[1] / "fixtures" / "live_tool_crash.py"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(fixture),
            str(tmp_path),
            phase,
            input_kind,
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
        calls, requests = [], []

        class Tool:
            spec = ToolSpec("probe", "crash fixture", {"type": "object"}, input_kind=input_kind)

            async def execute(self, call, context):
                calls.append(call)
                return ToolResult(call.id, call.name, "new")

        class Model:
            async def stream(self, request):
                requests.append(request)
                results = [item for item in request.items if isinstance(item, ToolResultItem)]
                assert len(results) == 1
                assert results[0].input_kind == input_kind
                saved = next(item.call for item in request.items if isinstance(item, ToolCallItem))
                assert saved.input_kind == input_kind
                if input_kind == "freeform":
                    assert saved.raw_arguments == "text('原样');\n" and saved.arguments is None
                if phase == "running":
                    assert results[0].is_error and "unknown" in results[0].content
                else:
                    assert results[0].content == ("new" if phase == "unclaimed" else "saved")
                yield ModelCompleted((AssistantMessageItem("recovered", turn.id, new_step_id()),))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Tool())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=repository.path,
            repository=repository,
            thread_id=thread,
            registry=registry,
            model=Model(),
        )
        try:
            events = [event async for event in runtime.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 1 and len(calls) == (phase == "unclaimed")
            marker = tmp_path / "side-effect.txt"
            assert (marker.read_text() if marker.exists() else "") == (
                "" if phase == "unclaimed" else "executed\n"
            )
            assert await repository.load_model_step(thread, turn.id, 0) is None
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
