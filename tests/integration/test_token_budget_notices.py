import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.models.types import ModelUsage
from corki.protocol.events import ContextCompacted, TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools import ToolRegistry


def budget_runtime(tmp_path, model, *, thread=None, scope="total", **options):
    from corki.config import TokenBudgetConfig

    return LangGraphRuntime.create(
        settings=CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            context_window_tokens=50_000,
            auto_compact_tokens=9000,
            auto_compact_token_limit_scope=scope,
            token_budget_enabled=True,
            token_budget=TokenBudgetConfig(**options),
        ),
        database_path=tmp_path / "sessions.db",
        registry=ToolRegistry(),
        model=model,
        thread_id=thread,
    )


class CompletingModel:
    def __init__(self, *, scope="total", output=9500):
        self.requests = []
        self.scope = scope
        self.output = output

    async def stream(self, request):
        self.requests.append(request)
        yield ModelCompleted(
            (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),),
            ModelUsage(1000 if self.scope == "body_after_prefix" else 0, self.output),
        )

    async def aclose(self):
        pass


@pytest.mark.parametrize("scope", ["total", "body_after_prefix"])
def test_nonterminal_assistant_step_records_positive_reminder_only_once(tmp_path, scope):
    from corki.protocol.items import BudgetNoticeItem

    class Model(CompletingModel):
        async def stream(self, request):
            self.requests.append(request)
            yield ModelCompleted(
                (AssistantMessageItem("working", request.items[-1].turn_id, new_step_id()),),
                ModelUsage(
                    1000 if scope == "body_after_prefix" else 0,
                    8000 if len(self.requests) == 1 else 8500,
                ),
                end_turn=len(self.requests) == 3,
            )

    async def scenario():
        model = Model()
        runtime = budget_runtime(
            tmp_path,
            model,
            scope=scope,
            reminder_threshold_tokens=1500,
            reminder_message_template="REMAIN {n_remaining}; AGAIN {n_remaining}; {unrelated}",
        )
        try:
            events = [event async for event in runtime.stream("WORK")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(model.requests) == 3
            for request in model.requests[1:]:
                notices = [item for item in request.items if isinstance(item, BudgetNoticeItem)]
                assert len(notices) == 1
                assert notices[0].content == "REMAIN 1000; AGAIN 1000; {unrelated}"
            notices = [
                item
                for item in await runtime._repository.load_items(runtime.thread_id)
                if isinstance(item, BudgetNoticeItem)
            ]
            assert len(notices) == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("scope", ["total", "body_after_prefix"])
def test_completion_records_once_across_turns_and_cold_runtime_then_new_window(tmp_path, scope):
    from corki.context.history import active_history
    from corki.memory.transcript import render_transcript
    from corki.protocol.items import BudgetNoticeItem, ContextItem

    async def scenario():
        options = dict(
            reminder_threshold_tokens=2000,
            reminder_message_template="REMINDER {n_remaining}",
            guidance_message="WORK_GUIDANCE",
            auto_compact_fallback_prompt="SAVE_BEFORE_RESET",
            auto_compact_fallback_buffer_tokens=4000,
        )
        model = CompletingModel(scope=scope)
        runtime = budget_runtime(tmp_path, model, scope=scope, **options)
        thread = runtime.thread_id
        try:
            for text in ("FIRST_FACT", "SECOND_FACT"):
                events = [event async for event in runtime.stream(text)]
                assert isinstance(events[-1], TurnCompleted)
                assert not any(isinstance(event, ContextCompacted) for event in events)
            assert len(model.requests) == 2  # Notifications must not force a continuation.
            for request in model.requests:
                keys = [item.key for item in request.items if isinstance(item, ContextItem)]
                assert keys.count("context_window_guidance") == 1
                assert keys.index("context_window_guidance") < keys.index("context_window")
            assert str(model.requests[-1].items).count("SAVE_BEFORE_RESET") == 1
            original = [
                item
                for item in await runtime._repository.load_items(thread)
                if isinstance(item, BudgetNoticeItem)
            ]
            assert [item.notice_kind for item in original] == ["reminder", "fallback"]
        finally:
            await runtime.aclose()
        cold = budget_runtime(tmp_path, model, thread=thread, scope=scope, **options)
        try:
            [event async for event in cold.stream("THIRD_FACT")]
            stored = await cold._repository.load_items(thread)
            assert [item for item in stored if isinstance(item, BudgetNoticeItem)] == original
            assert str(model.requests[-1].items).count("SAVE_BEFORE_RESET") == 1
            [event async for event in cold.compact()]
            [event async for event in cold.stream("FOURTH_FACT")]
            assert len(model.requests) == 4
            assert "SAVE_BEFORE_RESET" not in str(model.requests[-1].items)
            stored = await cold._repository.load_items(thread)
            notices = [item for item in stored if isinstance(item, BudgetNoticeItem)]
            assert len(notices) == 4 and len({item.id for item in notices}) == 4
            assert len({item.window_id for item in notices}) == 2
            assert sum(isinstance(item, BudgetNoticeItem) for item in active_history(stored)) == 2
            archive = render_transcript(stored, redact=lambda text: text)
            assert "FIRST_FACT" in archive and "FOURTH_FACT" in archive
            assert "SAVE_BEFORE_RESET" not in archive and "REMINDER" not in archive
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", ["new_context", "buffer_without_prompt", "hard_limit"])
def test_immediate_rollover_skips_fallback_even_with_large_buffer(tmp_path, reason):
    from corki.protocol.items import BudgetNoticeItem

    class Model(CompletingModel):
        async def stream(self, request):
            self.requests.append(request)
            turn = request.items[-1].turn_id
            if len(self.requests) == 1:
                name = "new_context" if reason == "new_context" else "get_context_remaining"
                yield ModelCompleted(
                    (ToolCallItem(ToolCall(new_tool_call_id(), name, {}), turn, new_step_id()),),
                    ModelUsage(0, 55_000 if reason == "hard_limit" else 9500),
                )
            else:
                yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))

    async def scenario():
        model = Model()
        runtime = budget_runtime(
            tmp_path,
            model,
            reminder_threshold_tokens=2000,
            auto_compact_fallback_prompt=None if reason == "buffer_without_prompt" else "FALLBACK",
            auto_compact_fallback_buffer_tokens=100_000,
        )
        try:
            events = [event async for event in runtime.stream("BEFORE_RESET")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(model.requests) == 2
            assert sum(isinstance(event, ContextCompacted) for event in events) == 1
            assert "BEFORE_RESET" not in str(model.requests[-1].items)
            notices = [
                item
                for item in await runtime._repository.load_items(runtime.thread_id)
                if isinstance(item, BudgetNoticeItem)
            ]
            assert [item.notice_kind for item in notices] == ["reminder"]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["pre_commit", "post_commit", "cancel"])
def test_notice_write_failure_does_not_replay_completed_sampling(tmp_path, failure, caplog):
    from corki.protocol.items import BudgetNoticeItem

    async def scenario():
        model = CompletingModel()
        runtime = budget_runtime(
            tmp_path,
            model,
            reminder_threshold_tokens=2000,
            auto_compact_fallback_prompt="FALLBACK",
            auto_compact_fallback_buffer_tokens=4000,
        )
        original = runtime._repository.append_items
        failed = False

        async def append(thread, items):
            nonlocal failed
            if any(isinstance(item, BudgetNoticeItem) for item in items) and not failed:
                failed = True
                if failure == "cancel":
                    raise asyncio.CancelledError
                if failure == "post_commit":
                    await original(thread, items)
                raise OSError("notice fixture")
            await original(thread, items)

        runtime._repository.append_items = append
        try:
            if failure == "cancel":
                events = []
                with pytest.raises(asyncio.CancelledError):
                    async for event in runtime.stream("FIRST"):
                        events.append(event)
                assert not any(isinstance(event, TurnCompleted) for event in events)
            else:
                events = [event async for event in runtime.stream("FIRST")]
                assert isinstance(events[-1], TurnCompleted)
            assert len(model.requests) == 1
            assert ("Could not persist token-budget guidance" in caplog.text) == (
                failure != "cancel"
            )
            [event async for event in runtime.stream("SECOND")]
            assert len(model.requests) == 2
            notices = [
                item
                for item in await runtime._repository.load_items(runtime.thread_id)
                if isinstance(item, BudgetNoticeItem)
            ]
            assert [item.notice_kind for item in notices] == ["reminder", "fallback"]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
def test_completed_usage_notices_reach_actual_provider_payload(tmp_path, mode):
    import json

    import httpx

    from corki.models import OpenAICompatibleModel, OpenAIResponsesModel, resolve_capabilities

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
                        "usage": {"input_tokens": 0, "output_tokens": 9500, "total_tokens": 9500},
                    },
                }
            else:
                packet = {
                    "choices": [
                        {"index": 0, "delta": {"content": "done"}, "finish_reason": "stop"}
                    ],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 9500, "total_tokens": 9500},
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
        runtime = budget_runtime(
            tmp_path,
            model,
            reminder_threshold_tokens=2000,
            reminder_message_template="REMINDER {n_remaining}",
            guidance_message="WORK_GUIDANCE",
            auto_compact_fallback_prompt="SAVE_BEFORE_RESET",
            auto_compact_fallback_buffer_tokens=4000,
        )
        try:
            for text in ("FIRST", "SECOND"):
                events = [event async for event in runtime.stream(text)]
                assert isinstance(events[-1], TurnCompleted)
            assert len(payloads) == 2
            key = "input" if mode == "responses" else "messages"
            messages = payloads[-1][key]
            notices = [
                item
                for item in messages
                if item.get("content") in ("REMINDER 0", "SAVE_BEFORE_RESET")
            ]
            assert len(notices) == 2
            assert all(
                item["role"] == ("developer" if mode == "responses" else "system")
                for item in notices
            )
            guidance = next(i for i, item in enumerate(messages) if "WORK_GUIDANCE" in str(item))
            window = next(i for i, item in enumerate(messages) if "<context_window>" in str(item))
            assert guidance < window
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("ending", ["new_context", "buffer", "complete"])
@pytest.mark.parametrize("scope", ["total", "body_after_prefix"])
def test_fallback_preserves_work_then_resets_without_summary(tmp_path, ending, scope):
    from corki.config.token_budget import TokenBudgetConfig

    async def scenario():
        requests, writes = [], []

        class Save:
            spec = ToolSpec("save", "Save a note", {"type": "object"})

            async def execute(self, call, context):
                writes.append(call.id)
                (tmp_path / "note.txt").write_text("saved work")
                return ToolResult(call.id, call.name, "saved")

        class Model:
            async def stream(self, request):
                requests.append(request)
                index = len(requests)
                turn = request.items[-1].turn_id
                if index <= 3 and not (ending == "complete" and index == 3):
                    name = (
                        "get_context_remaining"
                        if index == 1
                        else "save"
                        if index == 2
                        else "new_context"
                        if ending == "new_context"
                        else "get_context_remaining"
                    )
                    output = (
                        14_000 if index == 3 and ending == "buffer" else 9500 + 500 * (index - 1)
                    )
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(new_tool_call_id(), name, {}), turn, new_step_id()
                            ),
                        ),
                        ModelUsage(1000 if scope == "body_after_prefix" else 0, output),
                    )
                else:
                    yield ModelCompleted(
                        (AssistantMessageItem("done", turn, new_step_id()),), ModelUsage(100)
                    )

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Save())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                context_window_tokens=50_000,
                auto_compact_tokens=9000,
                auto_compact_token_limit_scope=scope,
                token_budget_enabled=True,
                token_budget=TokenBudgetConfig(
                    reminder_threshold_tokens=2000,
                    reminder_message_template="REMINDER {n_remaining}",
                    auto_compact_fallback_prompt="SAVE_BEFORE_RESET",
                    auto_compact_fallback_buffer_tokens=4000,
                ),
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("ORIGINAL_REQUEST")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(requests) == (3 if ending == "complete" else 4)
            for request in requests[1:3]:
                assert str(request.items).count("SAVE_BEFORE_RESET") == 1
                assert str(request.items).count("REMINDER 0") == 1
                assert "ORIGINAL_REQUEST" in str(request.items)
                assert "save" in {spec.name for spec in request.tools}
            assert len(writes) == 1 and (tmp_path / "note.txt").read_text() == "saved work"
            assert sum(isinstance(event, ContextCompacted) for event in events) == (
                ending != "complete"
            )
            if ending != "complete":
                assert "SAVE_BEFORE_RESET" not in str(requests[-1].items)
                assert "ORIGINAL_REQUEST" not in str(requests[-1].items)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
