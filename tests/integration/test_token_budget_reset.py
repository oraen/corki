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
from corki.protocol.events import ContextCompacted, TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("trigger", ["manual", "auto", "tool"])
@pytest.mark.parametrize("tool_mode", ["direct", "code_mode_only"])
def test_token_budget_resets_without_summarizing_or_clearing_environment(
    tmp_path, trigger, tool_mode
):
    async def scenario():
        requests = []
        note = tmp_path / "retained-note.txt"
        note.write_text("environment survives")

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn = request.items[-1].turn_id
                if len(requests) == 1:
                    items = (AssistantMessageItem("ASSISTANT_BEFORE", turn, new_step_id()),)
                    if trigger == "tool":
                        items = (
                            ToolCallItem(
                                ToolCall(new_tool_call_id(), "new_context", {}), turn, new_step_id()
                            ),
                        )
                    yield ModelCompleted(
                        items,
                        ModelUsage(500, 31_000) if trigger == "auto" else ModelUsage(500, 10),
                        end_turn=False if trigger == "auto" else None,
                    )
                else:
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                context_window_tokens=100_000,
                auto_compact_tokens=30_000,
                token_budget_enabled=True,
                tool_mode=tool_mode,
            ),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("USER_BEFORE")]
            if trigger == "manual":
                events += [event async for event in runtime.compact()]
                assert len(requests) == 1
                events += [event async for event in runtime.stream("USER_AFTER")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(requests) == 2
            assert sum(isinstance(event, ContextCompacted) for event in events) == 1
            assert "USER_BEFORE" not in str(requests[1].items)
            assert "ASSISTANT_BEFORE" not in str(requests[1].items)
            assert "new_context" in {tool.name for tool in requests[0].tools}
            first = next(
                item.content
                for item in requests[0].items
                if isinstance(item, ContextItem) and item.key == "context_window"
            )
            second = next(
                item.content
                for item in requests[1].items
                if isinstance(item, ContextItem) and item.key == "context_window"
            )
            initial_id = first.split("Current context window id: ")[1].splitlines()[0]
            assert f"First context window id: {initial_id}" in second
            assert f"Previous context window id: {initial_id}" in second
            assert f"Current context window id: {initial_id}" not in second
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert any(
                isinstance(item, UserMessageItem) and item.content == "USER_BEFORE"
                for item in stored
            )
            assert note.read_text() == "environment survives"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
def test_manual_reset_reaches_actual_http_without_summary_request(tmp_path, mode):
    from corki.memory.transcript import render_transcript

    async def scenario():
        payloads = []

        def respond(request):
            payloads.append(json.loads(request.content))
            if mode == "responses":
                packet = {
                    "type": "response.completed",
                    "response": {
                        "id": f"r-{len(payloads)}",
                        "output": [
                            {
                                "type": "message",
                                "id": f"i-{len(payloads)}",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "done"}],
                            }
                        ],
                    },
                }
            else:
                packet = {
                    "choices": [{"index": 0, "delta": {"content": "done"}, "finish_reason": "stop"}]
                }
            return httpx.Response(200, text=f"data: {json.dumps(packet)}\n\n")

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
                working_directory=tmp_path, skills_enabled=False, token_budget_enabled=True
            ),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=model,
        )
        try:
            [event async for event in runtime.stream("BEFORE_HTTP_RESET")]
            [event async for event in runtime.compact()]
            events = [event async for event in runtime.stream("AFTER_HTTP_RESET")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(payloads) == 2
            key = "messages" if mode == "chat_completions" else "input"
            assert "BEFORE_HTTP_RESET" not in str(payloads[-1][key])
            assert "AFTER_HTTP_RESET" in str(payloads[-1][key])
            windows = [item for item in payloads[-1][key] if "<context_window>" in str(item)]
            assert len(windows) == 1
            assert windows[0]["role"] == ("system" if mode == "chat_completions" else "developer")
            archive = render_transcript(
                await runtime._repository.load_items(runtime.thread_id), redact=lambda text: text
            )
            assert "BEFORE_HTTP_RESET" in archive and "AFTER_HTTP_RESET" in archive
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("after_commit", [False, True])
@pytest.mark.parametrize("tool_mode", ["direct", "code_mode_only"])
def test_reset_request_survives_storage_failure_and_cold_runtime(tmp_path, after_commit, tool_mode):
    from corki.protocol.events import TurnFailed
    from corki.protocol.items import CompactionItem

    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn = request.items[-1].turn_id
                if len(requests) == 1:
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(new_tool_call_id(), "new_context", {}), turn, new_step_id()
                            ),
                        )
                    )
                else:
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))

            async def aclose(self):
                pass

        def create(thread=None):
            return LangGraphRuntime.create(
                settings=CorkiSettings(
                    working_directory=tmp_path,
                    skills_enabled=False,
                    token_budget_enabled=True,
                    tool_mode=tool_mode,
                ),
                database_path=tmp_path / "sessions.db",
                registry=ToolRegistry(),
                model=Model(),
                thread_id=thread,
            )

        runtime = create()
        original = runtime._repository.append_items
        failed = False

        async def append(thread, items):
            nonlocal failed
            reset = any(isinstance(item, CompactionItem) and item.context_reset for item in items)
            if reset and not failed:
                failed = True
                if after_commit:
                    await original(thread, items)
                raise OSError("reset commit fixture failure")
            await original(thread, items)

        runtime._repository.append_items = append
        try:
            events = [event async for event in runtime.stream("BEFORE_FAILED_RESET")]
            assert isinstance(events[-1], TurnFailed)
            thread = runtime.thread_id
            await runtime.aclose()
            runtime = create(thread)
            events = [event async for event in runtime.stream("AFTER_FAILED_RESET")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(requests) == 2
            assert "BEFORE_FAILED_RESET" not in str(requests[-1].items)
            assert "AFTER_FAILED_RESET" in str(requests[-1].items)
            stored = await runtime._repository.load_items(thread)
            assert (
                sum(isinstance(item, CompactionItem) and item.context_reset for item in stored) == 1
            )
            assert (
                sum(
                    isinstance(item, ToolCallItem) and item.call.name == "new_context"
                    for item in stored
                )
                == 1
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("tool_mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("scope", ["total", "body_after_prefix"])
def test_remaining_tool_and_model_only_exposure(tmp_path, tool_mode, scope):
    from corki.code_mode.specs import nested_specs
    from corki.protocol.items import ToolResultItem

    async def scenario():
        requests = []
        registry = ToolRegistry()

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn = request.items[-1].turn_id
                if len(requests) == 1:
                    call = (
                        ToolCall(new_tool_call_id(), "get_context_remaining", {})
                        if tool_mode == "direct"
                        else ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments="text(await tools.get_context_remaining({}));",
                        )
                    )
                    yield ModelCompleted(
                        (ToolCallItem(call, turn, new_step_id()),), ModelUsage(1000)
                    )
                else:
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                context_window_tokens=100_000,
                auto_compact_tokens=30_000,
                token_budget_enabled=True,
                tool_mode=tool_mode,
                auto_compact_token_limit_scope=scope,
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=Model(),
        )
        try:
            assert "new_context" not in nested_specs(registry.snapshot())
            assert "get_context_remaining" in nested_specs(registry.snapshot())
            events = [event async for event in runtime.stream("inspect remaining context")]
            assert isinstance(events[-1], TurnCompleted)
            outputs = [item for item in requests[-1].items if isinstance(item, ToolResultItem)]
            assert len(outputs) == 1 and not outputs[0].is_error
            assert ("29000" if scope == "total" else "30000") in outputs[0].content
            assert ("tokens_left" in outputs[0].content) is (tool_mode == "code_mode_only")
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
