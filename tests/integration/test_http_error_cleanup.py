"""Non-success status survives diagnostic-body and response-close failures."""

import asyncio

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.events import ModelRetryScheduled, TurnCancelled, TurnCompleted, TurnFailed


@pytest.mark.parametrize("api", ["responses", "chat_completions"])
@pytest.mark.parametrize("status", [400, 429, 503])
@pytest.mark.parametrize("body_end", ["complete", "read_error", "cancel"])
@pytest.mark.parametrize("close_error", ["ordinary", "transport", "cancel"])
def test_error_body_cleanup_preserves_status_retry_budget_and_cancel(
    tmp_path, monkeypatch, api, status, body_end, close_error
):
    async def scenario():
        streams, requests, events = [], [], []

        class Body(httpx.AsyncByteStream):
            closes = 0

            async def __aiter__(self):
                yield b'{"error":{"message":"fixture HTTP rejection"}}'
                if body_end == "read_error":
                    raise httpx.ReadError("diagnostic body interrupted")
                if body_end == "cancel":
                    raise asyncio.CancelledError

            async def aclose(self):
                self.closes += 1
                if close_error == "cancel":
                    raise asyncio.CancelledError
                if close_error == "transport":
                    raise httpx.ReadError("secondary close transport error")
                raise RuntimeError("secondary close error")

        def handle(request):
            requests.append(request)
            body = Body()
            streams.append(body)
            return httpx.Response(status, stream=body)

        async def immediate(*args):
            await asyncio.sleep(0)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        monkeypatch.setattr(http_client, "OwnedHTTPClient", lambda **kwargs: client)
        monkeypatch.setattr("corki.models.http_stream.wait_http_retry", immediate)
        monkeypatch.setattr("corki.core.graph.wait_retry", immediate)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                api_mode=api,
                api_base="https://fixture.invalid/v1",
                api_key="fixture",
                model_max_retries=1,
            ),
            database_path=tmp_path / "session.db",
            home_path=tmp_path / "home",
            load_plugins=False,
        )
        cancelled = body_end == "cancel" or close_error == "cancel"
        try:
            try:
                async for event in runtime.stream("run"):
                    events.append(event)
            except asyncio.CancelledError:
                assert cancelled
            assert not any(isinstance(e, TurnCompleted) for e in events)
            terminal = events[-1]
            assert isinstance(terminal, TurnCancelled if cancelled else TurnFailed)
            assert len(requests) == (10 if status == 503 and not cancelled else 1)
            assert all(body.closes == 1 for body in streams)
            retries = [e for e in events if isinstance(e, ModelRetryScheduled)]
            assert len(retries) == (1 if status == 503 and not cancelled else 0)
            failure = await runtime._repository.load_model_failure(
                terminal.thread_id, terminal.turn_id, 0
            )
            if cancelled:
                assert failure is None
            else:
                assert failure.status_code == status
                assert (
                    failure.kind
                    == {
                        400: "invalid_request",
                        429: "retry_limit",
                        503: "server",
                    }[status]
                )
                assert "secondary close" not in failure.message
            assert (
                await runtime._repository.load_model_step(terminal.thread_id, terminal.turn_id, 0)
                is None
            )
            assert await runtime._repository.latest_running_turn(terminal.thread_id) is None
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())
