import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.events import ModelRetryScheduled, TurnCompleted, TurnFailed
from corki.protocol.ids import new_turn_id
from corki.protocol.items import AssistantMessageItem, CompactionItem, new_step_id
from corki.tools import ToolRegistry


def completed(api_mode, content):
    if api_mode == "responses":
        message = {
            "type": "message",
            "id": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": content}],
        }
        events = [
            {"type": "response.output_item.done", "item": message},
            {"type": "response.completed", "response": {"id": "response", "output": [message]}},
        ]
    else:
        events = [
            {
                "choices": [
                    {"delta": {"role": "assistant", "content": content}, "finish_reason": None}
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]
    data = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
    return data + ("data: [DONE]\n\n" if api_mode != "responses" else "")


@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("exhaust", [False, True])
def test_http_summary_retry_has_one_sampling_owner(tmp_path, monkeypatch, api_mode, exhaust):
    async def scenario():
        requests = []

        def handle(request):
            body = json.loads(request.content)
            requests.append(body)
            if len(requests) == 1 or exhaust:
                # Missing terminal response is retryable at either adapter's
                # sampling layer. The compaction owner must perform the retry.
                return httpx.Response(200, text="", headers={"content-type": "text/event-stream"})
            text = "summary from HTTP" if len(requests) == 2 else "done"
            return httpx.Response(
                200, text=completed(api_mode, text), headers={"content-type": "text/event-stream"}
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                api_mode=api_mode,
                api_key="fixture",
                api_base="https://fixture.invalid/v1",
                context_window_tokens=12000,
                auto_compact_tokens=3000,
                model_max_retries=1,
                model_retry_base_seconds=0.001,
            ),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
        )
        try:
            await runtime._ensure_ready()
            # Deliberately distinct nested budget: hidden adapter retries would
            # produce eight HTTP requests rather than the manager's two.
            runtime._model._max_retries = 7
            await runtime._repository.append_items(
                runtime.thread_id,
                (AssistantMessageItem("old " * 5000, new_turn_id(), new_step_id()),),
            )
            events = [event async for event in runtime.stream("current")]
            assert isinstance(events[-1], TurnFailed if exhaust else TurnCompleted), events[-1]
            assert len(requests) == (2 if exhaust else 3)
            assert requests[0] == requests[1]
            assert not requests[0].get("tools")
            retries = [e for e in events if isinstance(e, ModelRetryScheduled)]
            assert len(retries) == 1 and retries[0].purpose == "compaction"
            history = await runtime._repository.load_items(runtime.thread_id)
            assert sum(isinstance(i, CompactionItem) for i in history) == (not exhaust)
            terminal = events[-1]
            assert (
                await runtime._repository.load_model_failure(
                    terminal.thread_id, terminal.turn_id, 0
                )
                is None
            )
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


def test_main_responses_context_overflow_ends_turn_without_implicit_compaction(
    tmp_path, monkeypatch
):
    async def scenario():
        requests = []

        def handle(request):
            requests.append(request)
            payload = {
                "type": "response.failed",
                "response": {
                    "error": {"code": "context_length_exceeded", "message": "context fixture"}
                },
            }
            return httpx.Response(
                200,
                text="data: " + json.dumps(payload) + "\n\n",
                headers={"content-type": "text/event-stream"},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                api_mode="responses",
                api_key="fixture",
                api_base="https://fixture.invalid/v1",
                model_retry_base_seconds=0.001,
            ),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
        )
        try:
            events = [event async for event in runtime.stream("current")]
            assert isinstance(events[-1], TurnFailed) and events[-1].error_kind == "context_window"
            assert len(requests) == 1
            assert not any(isinstance(e, ModelRetryScheduled) for e in events)
            assert not any(
                isinstance(i, CompactionItem)
                for i in await runtime._repository.load_items(runtime.thread_id)
            )
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())
