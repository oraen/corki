"""Main sampling connection retries have their own, interruptible budget."""

import asyncio
import os
import sys
from pathlib import Path

import httpx
import pytest

import corki
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import (
    ModelCompleted,
    OpenAICompatibleModel,
    OpenAIResponsesModel,
    resolve_capabilities,
)
from corki.protocol.events import ModelRetryScheduled, TurnCompleted, TurnFailed
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize("fact", ["failure", "completed", "partial"])
def test_retry_node_does_not_repeat_wait_for_an_already_recorded_attempt(
    tmp_path, monkeypatch, fact
):
    from corki.models import ModelError, ModelErrorKind
    from corki.models.failure import ModelFailure
    from corki.protocol.items import AssistantMessageItem, new_step_id

    async def scenario():
        delays, calls = [], []

        async def fast_wait(delay, realtime):
            delays.append(delay)
            await asyncio.sleep(0)

        monkeypatch.setattr("corki.core.graph.wait_retry", fast_wait)

        class Repository(SQLiteSessionRepository):
            async def save_model_failure(self, thread, turn, index, failure):
                await super().save_model_failure(thread, turn, index, failure)
                if index == 0:
                    # Deterministic journal-ahead-of-checkpoint recovery boundary:
                    # this next attempt has already happened, so its wait is stale.
                    if fact == "failure":
                        await super().save_model_failure(
                            thread,
                            turn,
                            1,
                            ModelFailure(
                                "already failed",
                                ModelErrorKind.CONNECTION.value,
                                True,
                                0,
                                connection_retries_used=1,
                            ),
                        )
                    elif fact == "completed":
                        await self.commit_model_step(thread, turn, 1, ModelCompleted(()))
                    else:
                        await self.append_partial_item(
                            thread,
                            turn,
                            1,
                            AssistantMessageItem("durable partial", turn, new_step_id()),
                        )

        class Model:
            async def stream(self, request):
                calls.append(request)
                if len(calls) == 1:
                    raise ModelError(
                        "connection fixture", kind=ModelErrorKind.CONNECTION, retryable=True
                    )
                yield ModelCompleted(())

            async def aclose(self):
                pass

        repository = Repository(tmp_path / "sessions.db")
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=repository.path,
            repository=repository,
            model=Model(),
        )
        try:
            events = [event async for event in runtime.stream("run")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert delays == ([10] if fact == "failure" else [])
            assert len(calls) == (1 if fact == "completed" else 2)
            retries = [e.attempt for e in events if isinstance(e, ModelRetryScheduled)]
            assert retries == ([2] if fact == "failure" else [])
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("mode", ["default", "disabled", "mixed"])
@pytest.mark.parametrize("provider", [None, "openai", "bedrock", "amazon_bedrock", "independent"])
def test_connection_retries_are_separate_from_stream_budget(
    tmp_path, monkeypatch, api_mode, mode, provider
):
    async def scenario():
        requests, delays = [], []

        async def wait_retry(delay, realtime):
            delays.append(delay)
            await asyncio.sleep(0)

        monkeypatch.setattr("corki.core.graph.wait_retry", wait_retry)
        monkeypatch.setattr("corki.models.backoff.random.uniform", lambda low, high: 1.0)

        async def handle(request):
            endpoint = "responses" if api_mode == "responses" else "chat/completions"
            assert str(request.url) == f"https://fixture.invalid/v1/{endpoint}"
            requests.append(request)
            if mode == "mixed":
                error = httpx.ConnectError if len(requests) % 2 else httpx.ReadError
                raise error("fixture network failure", request=request)
            if len(requests) <= 6:
                raise httpx.ConnectTimeout("fixture connection timeout", request=request)
            if api_mode == "responses":
                text = 'data: {"type":"response.completed","response":{"id":"r","output":[]}}\n\n'
            else:
                text = 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
            return httpx.Response(200, text=text)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        adapter = OpenAIResponsesModel if api_mode == "responses" else OpenAICompatibleModel
        model = adapter(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            client=client,
            max_retries=7,  # Must not multiply the harness budget.
            request_max_retries=0,  # Isolate sampling/connection recovery from HTTP attempts.
            capabilities=resolve_capabilities(
                base_url="https://fixture.invalid/v1", api_mode=api_mode
            ),
        )
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                max_steps=1,
                model_max_retries=1,
                model_retry_base_seconds=0.001,
                provider_name=provider,
                **({"model_unbounded_connection_retries": False} if mode == "disabled" else {}),
            ),
            database_path=tmp_path / "sessions.db",
            model=model,
        )

        async def collect():
            return [event async for event in runtime.stream("run")]

        try:
            events = await asyncio.wait_for(collect(), 5)
            retries = [event for event in events if isinstance(event, ModelRetryScheduled)]
            actual = [(event.attempt, event.max_attempts) for event in retries]
            if mode == "default":
                assert isinstance(events[-1], TurnCompleted), events[-1]
                assert len(requests) == 7
                assert delays == [5, 10, 20, 40, 60, 60]
                assert actual == [(attempt, None) for attempt in range(1, 7)]
            elif mode == "mixed":
                assert isinstance(events[-1], TurnFailed), events[-1]
                assert len(requests) == 4
                assert delays == [5, 0.001, 10]
                assert actual == [(1, None), (1, 1), (2, None)]
            else:
                assert isinstance(events[-1], TurnFailed), events[-1]
                assert len(requests) == 2
                assert delays == [0.001]
                assert actual == [(1, 1)]
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())


def test_connection_backoff_survives_process_exit_before_checkpoint(tmp_path, monkeypatch):
    async def scenario():
        fixture = Path(__file__).parents[1] / "fixtures" / "retry_crash.py"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(fixture),
            str(tmp_path),
            "3",
            "connection",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                **os.environ,
                "PYTHONPATH": str(Path(corki.__file__).resolve().parent.parent),
                "CORKI_EXPECTED_PACKAGE": str(Path(corki.__file__).resolve().parent.parent),
            },
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
            assert process.returncode == 23, (stdout, stderr)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        thread = await repository.latest_thread(tmp_path)
        turn = await repository.latest_running_turn(thread)
        failure = await repository.load_model_failure(thread, turn.id, 3)
        assert (failure.retries_used, failure.connection_retries_used) == (0, 3)
        assert await repository.load_model_step(thread, turn.id, 3) is None
        requests, delays = [], []

        async def fast_wait(delay, realtime):
            delays.append(delay)
            await asyncio.sleep(0)

        monkeypatch.setattr("corki.core.graph.wait_retry", fast_wait)

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, model_max_retries=1
            ),
            database_path=repository.path,
            repository=repository,
            thread_id=thread,
            model=Model(),
        )
        try:
            events = [event async for event in runtime.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 1
            assert delays == [40]
            retries = [event for event in events if isinstance(event, ModelRetryScheduled)]
            assert [(event.attempt, event.max_attempts) for event in retries] == [(4, None)]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
