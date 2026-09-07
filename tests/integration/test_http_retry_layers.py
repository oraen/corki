"""Request retries precede error mapping; sampling retries have another budget."""

import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.events import ModelRetryScheduled, TurnCancelled, TurnCompleted, TurnFailed
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
@pytest.mark.parametrize(
    "status,error,kind,count,stream_retries",
    [
        (503, {"code": "server_is_overloaded"}, "server_overloaded", 5, 0),
        (503, {"code": "slow_down"}, "server_overloaded", 5, 0),
        (503, {"code": "unknown"}, "server", 10, 1),
        (500, {}, "server", 10, 1),
        (429, {"type": "usage_limit_reached"}, "usage_limit", 1, 0),
        (429, {"type": "usage_not_included"}, "usage_not_included", 1, 0),
        (429, {"code": "rate_limit_exceeded"}, "retry_limit", 1, 0),
        (400, {"code": "context_length_exceeded"}, "invalid_request", 1, 0),
        (400, {"code": "cyber_policy"}, "cyber_policy", 1, 0),
        (403, {"code": "misalignment_policy_violation"}, "misalignment_policy", 1, 0),
        (401, {}, "authentication", 2, 1),
        (403, {}, "authentication", 2, 1),
        (404, {}, "protocol", 2, 1),
    ],
)
def test_default_request_tier_precedes_stream_classification(
    tmp_path, monkeypatch, api_mode, status, error, kind, count, stream_retries
):
    async def scenario():
        requests = []

        def handle(request):
            requests.append(json.loads(request.content))
            return httpx.Response(status, json={"error": error}, headers={"Retry-After": "99"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        # Exercise Runtime's composition, not a separately configured injected model.
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                api_mode=api_mode,
                api_key="fixture",
                api_base="https://fixture.invalid/v1",
                model_max_retries=1,
                model_retry_base_seconds=0.001,
            ),
            database_path=repository.path,
            repository=repository,
        )

        async def collect():
            return [event async for event in runtime.stream("run")]

        try:
            events = await asyncio.wait_for(collect(), 3)
            assert isinstance(events[-1], TurnFailed), events[-1]
            assert events[-1].error_kind == kind
            assert len(requests) == count
            assert requests == [requests[0]] * count
            assert (
                len([event for event in events if isinstance(event, ModelRetryScheduled)])
                == stream_retries
            )
            terminal = events[-1]
            assert await repository.load_model_step(terminal.thread_id, terminal.turn_id, 0) is None
            assert (
                await repository.load_model_failure(
                    terminal.thread_id, terminal.turn_id, stream_retries
                )
                is not None
            )
            assert (
                await repository.load_model_failure(
                    terminal.thread_id, terminal.turn_id, stream_retries + 1
                )
                is None
            )
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["defaults", "http_recovers", "connection"])
def test_composed_budgets_and_successful_http_retry_not_a_failed_sample(
    tmp_path, monkeypatch, mode
):
    async def scenario():
        requests, http_delays, stream_delays = [], [], []

        async def http_wait(delay):
            http_delays.append(delay)
            await asyncio.sleep(0)

        async def stream_wait(delay, realtime):
            stream_delays.append(delay)
            await asyncio.sleep(0)

        monkeypatch.setattr("corki.models.http_stream.wait_http_retry", http_wait)
        monkeypatch.setattr("corki.core.graph.wait_retry", stream_wait)
        monkeypatch.setattr("corki.models.backoff.random.uniform", lambda low, high: 1.0)

        def handle(request):
            requests.append(request)
            if mode == "connection":
                if len(requests) <= 5:
                    raise httpx.ConnectError("network unavailable", request=request)
            elif mode == "defaults" or len(requests) < 3:
                return httpx.Response(503, text="unavailable", headers={"Retry-After": "99"})
            return httpx.Response(
                200, text='data: {"type":"response.completed","response":{"id":"done"}}\n\n'
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                api_mode="responses",
                api_key="fixture",
                api_base="https://fixture.invalid/v1",
            ),
            database_path=repository.path,
            repository=repository,
        )
        try:
            events = [event async for event in runtime.stream("run")]
            terminal = events[-1]
            assert isinstance(terminal, TurnFailed if mode == "defaults" else TurnCompleted), (
                terminal
            )
            if mode == "defaults":
                assert len(requests) == 30  # (4 + 1) HTTP attempts × (5 + 1) sampling attempts.
                assert http_delays == [0.2, 0.4, 0.8, 1.6] * 6
                assert stream_delays == [0.2, 0.4, 0.8, 1.6, 3.2]
                assert (
                    await repository.load_model_failure(terminal.thread_id, terminal.turn_id, 5)
                    is not None
                )
            elif mode == "http_recovers":
                assert len(requests) == 3
                assert http_delays == [0.2, 0.4]
                assert stream_delays == []
                assert (
                    await repository.load_model_failure(terminal.thread_id, terminal.turn_id, 0)
                    is None
                )
                assert (
                    await repository.load_model_step(terminal.thread_id, terminal.turn_id, 0)
                    is not None
                )
            else:
                assert len(requests) == 6
                assert http_delays == [0.2, 0.4, 0.8, 1.6]
                assert stream_delays == [5]
                failure = await repository.load_model_failure(
                    terminal.thread_id, terminal.turn_id, 0
                )
                assert (failure.kind, failure.retries_used, failure.connection_retries_used) == (
                    "connection",
                    0,
                    0,
                )
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["cancel", "steer"])
def test_http_backoff_closes_failed_response_and_is_interruptible(tmp_path, monkeypatch, action):
    async def scenario():
        waiting, requests, responses = asyncio.Event(), [], []

        async def wait(delay):
            assert responses[-1].is_closed
            waiting.set()
            await asyncio.Event().wait()

        monkeypatch.setattr("corki.models.http_stream.wait_http_retry", wait)

        def handle(request):
            requests.append(json.loads(request.content))
            if len(requests) == 1:
                response = httpx.Response(503, text="unavailable")
            else:
                assert requests[-1]["input"][-1]["content"][0]["text"] == "new input"
                response = httpx.Response(
                    200, text='data: {"type":"response.completed","response":{"id":"done"}}\n\n'
                )
            responses.append(response)
            return response

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                api_mode="responses",
                api_key="fixture",
                api_base="https://fixture.invalid/v1",
            ),
            database_path=tmp_path / "sessions.db",
        )
        events = []

        async def collect():
            try:
                async for event in runtime.stream("run", realtime=True):
                    events.append(event)
            except asyncio.CancelledError:
                assert action == "cancel"

        task = asyncio.create_task(collect())
        try:
            await asyncio.wait_for(waiting.wait(), 2)
            if action == "cancel":
                await runtime.cancel_active()
            else:
                await runtime.steer("new input")
            await asyncio.wait_for(task, 2)
            assert isinstance(events[-1], TurnCancelled if action == "cancel" else TurnCompleted), (
                events[-1]
            )
            assert len(requests) == (1 if action == "cancel" else 2)
            assert all(response.is_closed for response in responses)
        finally:
            await runtime.aclose()
            await asyncio.gather(task, return_exceptions=True)
            await client.aclose()

    asyncio.run(scenario())
