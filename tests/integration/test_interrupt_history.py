"""Cancellation is model-visible history, not a new user request or world-state slot."""

import asyncio
import json
from contextlib import suppress

import httpx
import pytest

from corki.config import CorkiSettings
from corki.context.window import active_history
from corki.core import LangGraphRuntime
from corki.memory.transcript import render_transcript
from corki.models import (
    ModelCompleted,
    OpenAICompatibleModel,
    OpenAIResponsesModel,
    resolve_capabilities,
)
from corki.protocol.events import TurnCancelled, TurnCompleted
from corki.protocol.items import AssistantMessageItem, UserMessageItem, item_kind, new_step_id
from corki.sessions import TurnStatus
from corki.tools import ToolRegistry


def markers(items):
    return [item for item in items if item_kind(item) == "turn_aborted"]


async def create(tmp_path, model, *, thread=None, enabled=True):
    settings = {"agent_interrupt_message_enabled": False} if not enabled else {}
    return await LangGraphRuntime.acreate(
        settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False, **settings),
        database_path=tmp_path / "sessions.db",
        registry=ToolRegistry(),
        model=model,
        thread_id=thread,
    )


async def cancel_turn(runtime, started):
    events = []

    async def consume():
        with suppress(asyncio.CancelledError):
            async for event in runtime.stream("interrupted request", realtime=True):
                events.append(event)

    consumer = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(started.wait(), 3)
        await runtime.cancel_active()
        await runtime.cancel_active()
        await asyncio.wait_for(consumer, 3)
        assert isinstance(events[-1], TurnCancelled)
        return events[-1]
    finally:
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("reopen", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
def test_interrupt_marker_is_durable_once_and_reaches_actual_http_request(
    tmp_path, mode, reopen, enabled
):
    async def scenario():
        started = asyncio.Event()
        payloads = []

        def respond(request):
            payloads.append(json.loads(request.content))
            body = (
                'data: {"choices":[{"index":0,"delta":{"content":"done"},'
                '"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
                if mode == "chat_completions"
                else 'data: {"type":"response.completed","response":{"id":"r"}}\n\n'
            )
            return httpx.Response(200, text=body)

        class Model:
            def __init__(self, block):
                self.block = block
                adapter = (
                    OpenAICompatibleModel if mode == "chat_completions" else OpenAIResponsesModel
                )
                self.adapter = adapter(
                    api_key="test",
                    base_url="https://api.openai.com/v1",
                    capabilities=resolve_capabilities(
                        base_url="https://api.openai.com/v1", api_mode=mode
                    ),
                    client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
                )

            async def stream(self, request):
                if self.block:
                    self.block = False
                    started.set()
                    await asyncio.Event().wait()
                async for event in self.adapter.stream(request):
                    yield event

            async def aclose(self):
                await self.adapter.aclose()

        runtime = await create(tmp_path, Model(True), enabled=enabled)
        try:
            terminal = await cancel_turn(runtime, started)
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert len(markers(stored)) == int(enabled)
            if enabled:
                assert markers(stored)[0] == stored[-1]
                assert markers(stored)[0].turn_id == terminal.turn_id
            if reopen:
                thread = runtime.thread_id
                await runtime.aclose()
                runtime = await create(tmp_path, Model(False), thread=thread, enabled=enabled)
            for _ in range(2):
                events = [event async for event in runtime.stream("next")]
                assert isinstance(events[-1], TurnCompleted)
            for payload in payloads:
                messages = payload["messages" if mode == "chat_completions" else "input"]
                found = [message for message in messages if "<turn_aborted>" in str(message)]
                assert len(found) == int(enabled)
                if enabled:
                    assert found[0]["role"] == "user"
                    assert "may have partially executed" in str(found[0])
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert len(markers(stored)) == int(enabled)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_marker_is_compaction_input_not_retained_user_but_stays_in_memory_archive(tmp_path):
    async def scenario():
        started = asyncio.Event()
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    started.set()
                    await asyncio.Event().wait()
                yield ModelCompleted(
                    (AssistantMessageItem("summary", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await create(tmp_path, Model())
        try:
            await cancel_turn(runtime, started)
            compacted = [event async for event in runtime.compact()]
            assert isinstance(compacted[-1], TurnCompleted)
            assert len(markers(requests[1].items)) == 1
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert len(markers(stored)) == 1
            assert not markers(active_history(stored))
            assert [
                i.content for i in active_history(stored) if isinstance(i, UserMessageItem)
            ] == ["interrupted request"]
            archive = json.loads(render_transcript(stored, redact=lambda text: text))
            assert sum(row["type"] == "turn_aborted" for row in archive) == 1
            await runtime.aclose()

            class MemoryModel:
                def __init__(self):
                    self.requests = []

                async def stream(self, request):
                    self.requests.append(request)
                    if request.output_schema_name == "corki_memory_extraction":
                        assert request.output_schema is not None and not request.tools
                        assert '"type":"turn_aborted"' in request.items[0].content
                        assert "may have partially executed" in request.items[0].content
                        value = {
                            "raw_memory": "The earlier task was interrupted; verify partial work.",
                            "rollout_summary": "Interrupted task followed by compaction.",
                            "rollout_slug": "interrupted",
                        }
                    else:
                        assert request.output_schema is None and request.tools
                        value = {
                            "memory": "Verify interrupted work.",
                            "memory_summary": "Interrupted work index.",
                            "skills": [],
                        }
                    yield ModelCompleted(
                        (
                            AssistantMessageItem(
                                json.dumps(value),
                                request.items[-1].turn_id,
                                new_step_id(),
                            ),
                        )
                    )

            memory_model = MemoryModel()
            later = await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    skills_enabled=False,
                    memories_enabled=True,
                    memories_min_thread_idle_hours=0,
                ),
                database_path=tmp_path / "sessions.db",
                registry=ToolRegistry(),
                model=Model(),
                memory_model=memory_model,
                memory_root=tmp_path / "memories",
            )
            try:
                [event async for event in later.stream("initialize memory")]
                report = await later._memory_service.wait()
                assert report.extracted == 1 and report.consolidated and report.failed == 0
                assert len(memory_model.requests) == 2
            finally:
                await later.aclose()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("enabled_after", [False, True])
def test_committed_marker_prevents_cold_resume_after_cancel_terminal_write_failure(
    tmp_path, enabled_after
):
    async def scenario():
        started = asyncio.Event()
        calls = []

        class Model:
            async def stream(self, request):
                calls.append(request)
                started.set()
                await asyncio.Event().wait()
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = await create(tmp_path, Model())
        original = runtime._repository.save_turn

        async def save(record):
            if record.status is TurnStatus.CANCELLED:
                raise OSError("cancel terminal persistence fault")
            await original(record)

        runtime._repository.save_turn = save
        runtime._repository.retry_turn_terminal = save
        try:
            await cancel_turn(runtime, started)
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is not None
            assert len(markers(await runtime._repository.load_items(runtime.thread_id))) == 1
            thread = runtime.thread_id
            # Lose process-owned writes; keep durable cancellation facts for cold recovery.
            runtime._pending_terminals.clear()
            await runtime.aclose()
            runtime = await create(tmp_path, Model(), thread=thread, enabled=enabled_after)
            events = []

            async def recover():
                with suppress(asyncio.CancelledError):
                    async for event in runtime.resume_pending():
                        events.append(event)

            await asyncio.wait_for(recover(), 3)
            assert isinstance(events[-1], TurnCancelled)
            assert len(calls) == 1
            assert await runtime._repository.latest_running_turn(thread) is None
            assert len(markers(await runtime._repository.load_items(thread))) == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_compact_replacement_does_not_claim_user_interruption(tmp_path):
    async def scenario():
        started = asyncio.Event()
        requests, events = [], []

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    started.set()
                    await asyncio.Event().wait()
                yield ModelCompleted(
                    (AssistantMessageItem("summary", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await create(tmp_path, Model())

        async def consume():
            with suppress(asyncio.CancelledError):
                async for event in runtime.stream("replace me"):
                    events.append(event)

        consumer = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(started.wait(), 3)
            result = [event async for event in runtime.compact()]
            await consumer
            assert isinstance(events[-1], TurnCancelled)
            assert isinstance(result[-1], TurnCompleted)
            assert not markers(await runtime._repository.load_items(runtime.thread_id))
            assert not any(markers(request.items) for request in requests)
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["completed", "error", "self_cancel"])
def test_only_explicit_interruption_adds_marker(tmp_path, outcome):
    async def scenario():
        class Model:
            async def stream(self, request):
                if outcome == "error":
                    raise ValueError("model fixture fault")
                if outcome == "self_cancel":
                    raise asyncio.CancelledError
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = await create(tmp_path, Model())
        try:
            with suppress(asyncio.CancelledError):
                [event async for event in runtime.stream("request")]
            assert not markers(await runtime._repository.load_items(runtime.thread_id))
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_marker_write_failure_preserves_cancel_and_reports_missing_history(tmp_path, caplog):
    async def scenario():
        started = asyncio.Event()

        class Model:
            async def stream(self, request):
                started.set()
                await asyncio.Event().wait()
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = await create(tmp_path, Model())
        append = runtime._repository.append_items

        async def failing_append(thread, items):
            if markers(items):
                raise OSError("marker storage unavailable")
            await append(thread, items)

        runtime._repository.append_items = failing_append
        try:
            await cancel_turn(runtime, started)
            assert not markers(await runtime._repository.load_items(runtime.thread_id))
            assert "marker storage unavailable" in caplog.text
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_code_mode_nested_cleanup_precedes_marker(tmp_path):
    from corki.code_mode.service import CodeModeService
    from corki.protocol.ids import new_tool_call_id
    from corki.protocol.items import ToolCallItem
    from corki.protocol.tools import ToolCall, ToolSpec

    if not CodeModeService.available():
        pytest.skip("install corki[code-mode]")

    async def scenario():
        started, cleaned = asyncio.Event(), asyncio.Event()
        requests = []

        class Hold:
            spec = ToolSpec("hold", "fixture side effect", {"type": "object"})

            async def execute(self, call, context):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaned.set()

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(
                                    new_tool_call_id(),
                                    "exec",
                                    None,
                                    raw_arguments=(
                                        '// @exec: {"yield_time_ms":0}\nawait tools.hold({});'
                                    ),
                                    input_kind="freeform",
                                ),
                                request.items[-1].turn_id,
                                new_step_id(),
                            ),
                        )
                    )
                else:
                    await asyncio.Event().wait()

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Hold())
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, tool_mode="code_mode_only"
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=Model(),
        )
        interrupted_results = []
        claim = runtime._repository.claim_tool_call
        append = runtime._repository.append_items

        async def claim_call(thread, turn, call):
            result = await claim(thread, turn, call)
            if call.name == "hold" and result is not None:
                interrupted_results.append(result)
            return result

        async def append_items(thread, items):
            if markers(items):
                assert interrupted_results and cleaned.is_set()
            await append(thread, items)

        runtime._repository.claim_tool_call = claim_call
        runtime._repository.append_items = append_items
        try:
            await cancel_turn(runtime, started)
            history = await runtime._repository.load_items(runtime.thread_id)
            assert cleaned.is_set() and not runtime._code_mode.cells
            assert interrupted_results and all(result.is_error for result in interrupted_results)
            assert markers(history) == [history[-1]]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
