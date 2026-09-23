import asyncio
import json

import httpx
import pytest

from corki import http_client
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
@pytest.mark.parametrize("failure_kind", ["missing_terminal", "authentication"])
def test_http_summary_retry_has_one_sampling_owner(
    tmp_path, monkeypatch, api_mode, exhaust, failure_kind
):
    async def scenario():
        requests = []

        def handle(request):
            body = json.loads(request.content)
            requests.append(body)
            if len(requests) == 1 or exhaust:
                if failure_kind == "authentication":
                    # Local compaction deliberately owns a broader retry loop
                    # than ordinary sampling, matching compact.rs's catch-all.
                    return httpx.Response(401, json={"error": {"message": "summary denied"}})
                # Missing terminal response is retryable at either adapter's
                # sampling layer. The compaction owner must perform the retry.
                return httpx.Response(200, text="", headers={"content-type": "text/event-stream"})
            text = "summary from HTTP" if len(requests) == 2 else "done"
            return httpx.Response(
                200, text=completed(api_mode, text), headers={"content-type": "text/event-stream"}
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        monkeypatch.setattr(http_client, "OwnedHTTPClient", lambda **kwargs: client)
        runtime = await LangGraphRuntime.acreate(
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
            if failure_kind == "authentication":
                assert "summary denied" in retries[0].error
                if exhaust:
                    assert events[-1].error_kind == "authentication"
                    assert "summary denied" in events[-1].error
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
        monkeypatch.setattr(http_client, "OwnedHTTPClient", lambda **kwargs: client)
        runtime = await LangGraphRuntime.acreate(
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


@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("manual", [False, True])
def test_payment_required_stops_summary_without_retry_or_history_replacement(
    tmp_path, monkeypatch, api_mode, manual
):
    async def scenario():
        requests = []

        def handle(request):
            assert request.url.host == "fixture.invalid"
            assert request.url.path == (
                "/v1/responses" if api_mode == "responses" else "/v1/chat/completions"
            )
            requests.append(json.loads(request.content))
            return httpx.Response(402, json={"error": {"message": "Insufficient Balance"}})

        client_type = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda **kwargs: client_type(**kwargs, transport=httpx.MockTransport(handle)),
        )
        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            api_mode=api_mode,
            api_key="fixture",
            api_base="https://fixture.invalid/v1",
            context_window_tokens=12000,
            auto_compact_tokens=3000,
            model_max_retries=2,
            model_retry_base_seconds=0.001,
        )
        database = tmp_path / "state.db"
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            registry=ToolRegistry(),
            database_path=database,
            home_path=tmp_path,
        )
        try:
            await runtime._ensure_ready()
            thread = runtime.thread_id
            original = (AssistantMessageItem("old " * 5000, new_turn_id(), new_step_id()),)
            await runtime._repository.append_items(thread, original)
            events = [
                event
                async for event in (runtime.compact() if manual else runtime.stream("current"))
            ]
            assert isinstance(events[-1], TurnFailed), events[-1]
            assert "Insufficient Balance" in events[-1].error
            assert not events[-1].retryable
            assert len(requests) == 1
            assert not requests[0].get("tools")
            assert not any(isinstance(event, ModelRetryScheduled) for event in events)
            stored = await runtime._repository.load_items(thread)
            assert stored[: len(original)] == original
            assert not any(isinstance(item, CompactionItem) for item in stored)
        finally:
            await runtime.aclose()
        cold = await LangGraphRuntime.acreate(
            settings=settings,
            registry=ToolRegistry(),
            database_path=database,
            thread_id=thread,
            home_path=tmp_path,
        )
        try:
            assert [event async for event in cold.resume_pending()] == []
            assert len(requests) == 1
            assert await cold._repository.load_items(thread) == stored
        finally:
            await cold.aclose()

    asyncio.run(scenario())
