"""Real I/O failure budgets using isolated clients, never a user's gateway."""

import asyncio
import json
import sys
import uuid
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "bridge"))
from model_watch import ModelCallWatch

from corki.models.base import ModelError
from corki.models.http_stream import model_http_stream
from corki.models.request_watch import request_observer, request_wait, response_lines


def test_failure_thresholds_and_wrapped_error_counting():
    async def check():
        warnings = []

        async def notify(value):
            warnings.append(value)

        watch = ModelCallWatch(notify)
        assert (watch.warning_seconds, watch.failure_seconds) == (30, 60)
        watch.begin()
        for count in range(1, 60):
            error = httpx.ConnectError("fixture refused")
            await watch.failure(error)
            wrapped = ModelError("wrapped")
            wrapped.__cause__ = error
            await watch.failure(wrapped)
            assert watch.failures == count
            assert watch.warning == (count >= 30)
            assert watch.warning_reason == ("failure" if count >= 30 else None)
        with pytest.raises(ModelError, match="^模型调用失败$") as failure:
            await watch.failure(httpx.ConnectError("sixtieth failure"))
        assert not failure.value.retryable
        assert warnings == [True, False]
        await watch.close()

    asyncio.run(check())


def test_warning_reason_changes_without_changing_failure_policy():
    async def check():
        warnings = []

        async def notify(value):
            warnings.append((value, watch.warning_reason))

        watch = ModelCallWatch(notify)
        watch.begin()
        watch.warn(watch.generation)
        await asyncio.sleep(0)
        assert warnings == [(True, "idle")]
        await watch.failure(httpx.ConnectError("fixture refused"))
        assert warnings[-1] == (True, "failure")
        assert watch.failures == 1 and not watch.expired
        await watch.activity()
        assert warnings[-1] == (False, None)
        await watch.complete()
        assert watch.failures == 0
        await watch.close()

    asyncio.run(check())


def test_silence_warns_then_fails_and_cancels_real_wait():
    async def check():
        warnings, cancelled = [], asyncio.Event()

        async def notify(value):
            warnings.append(value)

        async def stalled():
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        watch = ModelCallWatch(notify, warning_seconds=.03, failure_seconds=.08)
        watch.begin()
        with pytest.raises(ModelError, match="^模型调用失败$"):
            await watch.wait(stalled())
        assert cancelled.is_set() and warnings == [True, False]
        await watch.close()

    asyncio.run(check())


def test_stream_activity_and_tools_do_not_trigger_timeout():
    async def check():
        warnings = []

        async def notify(value):
            warnings.append(value)

        watch = ModelCallWatch(notify, warning_seconds=.03, failure_seconds=.08)
        watch.begin()
        for _ in range(8):
            await watch.wait(asyncio.sleep(.015))
            await watch.activity()
        assert not warnings
        # A finished response suspends monitoring while tools/approvals execute.
        await watch.complete()
        await watch.wait(asyncio.sleep(.1))
        assert not warnings and watch.failures == 0
        await watch.close()

    asyncio.run(check())


@pytest.mark.parametrize("mode", ["connection", "http"])
def test_actual_http_attempts_count_once(mode):
    async def check():
        warnings, attempts = [], []

        async def notify(value):
            warnings.append(value)

        async def handler(request):
            attempts.append(request)
            if mode == "connection":
                raise httpx.ConnectError("fixture refused")
            return httpx.Response(503, json={"error": {"message": "fixture offline"}})

        watch = ModelCallWatch(notify)
        token = request_observer.set(watch)
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with pytest.raises(ModelError, match="^模型调用失败$"):
                    async with model_http_stream(
                        client, "http://fixture.invalid/responses", headers={}, payload={},
                        max_retries=60, base_seconds=0,
                    ):
                        pytest.fail("An unavailable peer cannot produce a response")
            assert len(attempts) == 60 and warnings == [True, False]
        finally:
            await watch.close()
            request_observer.reset(token)

    asyncio.run(check())


def test_sse_stall_and_graph_retry_delay_share_deadline():
    async def check():
        warnings = []

        async def notify(value):
            warnings.append(value)

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"data: partial\n\n"
                await asyncio.Future()

        watch = ModelCallWatch(notify, warning_seconds=.03, failure_seconds=.08)
        token = request_observer.set(watch)
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, stream=Stream())
            )) as client:
                with pytest.raises(ModelError, match="^模型调用失败$"):
                    async with model_http_stream(
                        client, "http://fixture.invalid/responses", headers={}, payload={},
                        max_retries=0, base_seconds=0,
                    ) as response:
                        lines = [line async for line in response_lines(response)]
                        assert lines  # Unreachable: the stream stalls after valid content.
            assert warnings == [True, False]
            await watch.close()
            watch = ModelCallWatch(notify, warning_seconds=.03, failure_seconds=.08)
            request_observer.set(watch)
            watch.begin()
            with pytest.raises(ModelError, match="^模型调用失败$"):
                await request_wait(asyncio.sleep(10))
        finally:
            await watch.close()
            request_observer.reset(token)

    asyncio.run(check())


@pytest.mark.parametrize("mode", ["connection", "partial"])
@pytest.mark.parametrize("api_mode", ["chat_completions", "responses"])
def test_real_runtime_fails_durably_and_keeps_partial_answer(tmp_path, monkeypatch, mode, api_mode):
    import service as bridge_service

    from corki.core.runtime import LangGraphRuntime
    from corki.models.capabilities import ApiMode, ProviderCapabilities
    from corki.models.openai_compatible import OpenAICompatibleModel
    from corki.models.responses import OpenAIResponsesModel
    from corki.tools.registry import ToolRegistry

    async def check():
        (tmp_path / "settings.json").write_text(json.dumps({
            "default": {"provider": "fixture", "model": "fixture-model"},
            "providers": {"fixture": {
                "api_key": "fixture-only", "base_url": "http://fixture.invalid/v1",
                "models": {"fixture-model": {"context_window": 262144}},
            }},
        }))
        events, released = [], asyncio.Event()

        class Partial(httpx.AsyncByteStream):
            async def __aiter__(self):
                if api_mode == "responses":
                    data = {"type": "response.output_text.delta", "item_id": "message",
                            "output_index": 0, "content_index": 0, "delta": "partial answer"}
                else:
                    data = {"choices": [{"index": 0, "delta": {"content": "partial answer"}}]}
                yield ("data: " + json.dumps(data) + "\n\n").encode()
                await asyncio.Future()

            async def aclose(self):
                released.set()

        async def handler(request):
            if mode == "connection":
                raise httpx.ConnectError("fixture refused")
            return httpx.Response(200, stream=Partial())

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        model_class = OpenAIResponsesModel if api_mode == "responses" else OpenAICompatibleModel
        model = model_class(
            api_key="fixture-only", base_url="http://fixture.invalid/v1", client=client,
            capabilities=ProviderCapabilities("fixture", ApiMode(api_mode)),
            request_max_retries=0,
        )

        async def factory(**kw):
            kw["settings"] = replace(
                kw["settings"], skills_enabled=False, memories_generate=False,
                memories_use=False,
            )
            return await LangGraphRuntime.acreate(
                **kw, model=model, registry=ToolRegistry(), load_plugins=False
            )

        async def send(event):
            events.append(event)

        monkeypatch.setattr(bridge_service, "ModelCallWatch", lambda notify: ModelCallWatch(
            notify, warning_seconds=.03, failure_seconds=.08
        ))
        service = bridge_service.Service(tmp_path, send, factory)
        request = {"requestId": str(uuid.uuid4()), "threadId": str(uuid.uuid4()),
                   "provider": "fixture", "model": "fixture-model", "content": "hello"}
        try:
            await service.submit(request)
            await asyncio.wait_for(asyncio.gather(*service.tasks.values()), 5)
            receipt = await service.request_read({"requestId": request["requestId"]})
            assert receipt["state"] == "failed" and receipt["error"] == "模型调用失败"
            health = [e["params"]["data"]["warning"] for e in events
                      if e.get("method") == "event" and e["params"]["type"] == "model.health"]
            assert health == [True, False]
            reasons = [e["params"]["data"]["reason"] for e in events
                       if e.get("method") == "event" and e["params"]["type"] == "model.health"]
            assert reasons == ["failure" if mode == "connection" else "idle", None]
            history = await service.read_thread({"threadId": request["threadId"]})
            assert not history["running"]
            assert history["messages"][-1]["content"] == "模型调用失败"
            assert history["messages"][-1]["role"] == "assistant"
            assert not history["messages"][-1]["error"]
            assert history["turns"][-1]["status"] == "failed"
            if mode == "partial":
                assert released.is_set()
                assert any(m["content"] == "partial answer" for m in history["messages"])
        finally:
            await service.close()
            await client.aclose()

    asyncio.run(check())
