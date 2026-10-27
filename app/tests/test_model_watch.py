"""Connectivity notifications and real Runtime retries, with isolated fake peers."""

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

from corki.models.base import ModelError, ModelErrorKind


def test_connection_failures_have_no_desktop_budget_and_wrappers_count_once():
    async def check():
        warnings = []

        async def notify(value):
            warnings.append((value, watch.warning_reason))

        watch = ModelCallWatch(notify, warning_seconds=0.01)
        watch.begin()
        for count in range(1, 101):
            error = httpx.ConnectError("fixture refused")
            await watch.failure(error)
            wrapped = ModelError("wrapped", kind=ModelErrorKind.CONNECTION, retryable=True)
            wrapped.__cause__ = error
            await watch.failure(wrapped)
            assert watch.failures == count
        assert warnings == [(True, "connection")]
        # A Runtime backoff must not inherit an elapsed desktop deadline.
        await watch.wait(asyncio.sleep(0.08))
        assert watch.warning_reason == "connection"
        await watch.activity()
        assert not watch.warning and watch.failures == 0
        await watch.complete()
        assert warnings[-1] == (False, None) and watch.failures == 0
        await watch.close()

    asyncio.run(check())


def test_silence_only_warns_and_cancellation_still_closes_wait():
    async def check():
        warnings, cancelled = [], asyncio.Event()

        async def notify(value):
            warnings.append((value, watch.warning_reason))

        async def stalled():
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        watch = ModelCallWatch(notify, warning_seconds=0.01)
        watch.begin()
        task = asyncio.create_task(watch.wait(stalled()))
        await asyncio.sleep(0.08)
        assert not task.done() and warnings == [(True, "idle")]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()
        await watch.close()
        assert warnings[-1] == (False, None)

    asyncio.run(check())


@pytest.mark.parametrize(
    "status,kind",
    [
        (400, ModelErrorKind.INVALID_REQUEST),
        (401, ModelErrorKind.AUTHENTICATION),
        (404, ModelErrorKind.PROTOCOL),
    ],
)
def test_terminal_error_is_a_normal_model_failure_with_metadata(status, kind):
    async def check():
        async def notify(value):
            pass

        watch = ModelCallWatch(notify)
        error = ModelError("private diagnostic", kind=kind, status_code=status)
        with pytest.raises(ModelError, match="^模型调用失败$") as failure:
            await watch.failure(error)
        assert failure.value.status_code == status and failure.value.kind == kind
        assert not failure.value.retryable and failure.value.__cause__ is error
        await watch.close()

    asyncio.run(check())


def test_core_recoverable_errors_keep_their_classification_and_message():
    async def check():
        async def notify(value):
            pass

        watch = ModelCallWatch(notify)
        for kind in (ModelErrorKind.CONTEXT_WINDOW, ModelErrorKind.INVALID_IMAGE):
            error = ModelError("core recovery detail", kind=kind)
            await watch.failure(error)
            assert str(error) == "core recovery detail"
        await watch.close()

    asyncio.run(check())


@pytest.mark.parametrize("api_mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("mode", ["recover", "stop", "unavailable", "server", "partial"])
def test_real_runtime_network_recovery_terminal_errors_and_stop(
    tmp_path, monkeypatch, mode, api_mode
):
    import service as bridge_service

    from corki.core.runtime import LangGraphRuntime
    from corki.models.capabilities import ApiMode, ProviderCapabilities
    from corki.models.openai_compatible import OpenAICompatibleModel
    from corki.models.responses import OpenAIResponsesModel
    from corki.tools.registry import ToolRegistry

    async def check():
        (tmp_path / "settings.json").write_text(
            json.dumps(
                {
                    "default": {"provider": "fixture", "model": "fixture-model"},
                    "providers": {
                        "fixture": {
                            "api_key": "fixture-only",
                            "base_url": "http://fixture.invalid/v1",
                            "models": {"fixture-model": {"context_window": 262144}},
                        }
                    },
                }
            )
        )
        events, attempts, delays = [], [], []
        released, entered, retry_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class Partial(httpx.AsyncByteStream):
            async def __aiter__(self):
                if api_mode == "responses":
                    data = {
                        "type": "response.output_text.delta",
                        "item_id": "message",
                        "output_index": 0,
                        "content_index": 0,
                        "delta": "partial answer",
                    }
                else:
                    data = {"choices": [{"index": 0, "delta": {"content": "partial answer"}}]}
                yield ("data: " + json.dumps(data) + "\n\n").encode()
                entered.set()
                await asyncio.Future()

            async def aclose(self):
                released.set()

        async def handler(request):
            attempts.append(request)
            if mode in ("recover", "stop") and (mode == "stop" or len(attempts) <= 65):
                raise httpx.ConnectError("fixture disconnected")
            if mode == "unavailable":
                return httpx.Response(400, json={"error": {"message": "fixture unavailable"}})
            if mode == "server":
                return httpx.Response(503, json={"error": {"message": "fixture transient"}})
            if mode == "partial":
                return httpx.Response(200, stream=Partial())
            body = (
                'data: {"type":"response.completed","response":{"id":"r","output":[]}}\n\n'
                if api_mode == "responses"
                else 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
            )
            return httpx.Response(200, text=body)

        from corki.core.retry import wait_retry

        async def controlled_wait(delay, realtime):
            delays.append(delay)
            retry_entered.set()
            if mode == "stop":
                return await wait_retry(delay, realtime)
            await asyncio.sleep(0)

        monkeypatch.setattr("corki.core.graph.wait_retry", controlled_wait)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        model_class = OpenAIResponsesModel if api_mode == "responses" else OpenAICompatibleModel
        model = model_class(
            api_key="fixture-only",
            base_url="http://fixture.invalid/v1",
            client=client,
            capabilities=ProviderCapabilities("fixture", ApiMode(api_mode)),
            request_max_retries=0,
        )

        async def factory(**kw):
            kw["settings"] = replace(
                kw["settings"],
                skills_enabled=False,
                memories_generate=False,
                memories_use=False,
                model_max_retries=1,
            )
            return await LangGraphRuntime.acreate(
                **kw, model=model, registry=ToolRegistry(), load_plugins=False
            )

        async def send(event):
            events.append(event)

        monkeypatch.setattr(
            bridge_service,
            "ModelCallWatch",
            lambda notify: ModelCallWatch(notify, warning_seconds=0.01),
        )
        service = bridge_service.Service(tmp_path, send, factory)
        request = {
            "requestId": str(uuid.uuid4()),
            "threadId": str(uuid.uuid4()),
            "provider": "fixture",
            "model": "fixture-model",
            "content": "hello",
        }
        try:
            await service.submit(request)
            if mode in ("stop", "partial"):
                await asyncio.wait_for((retry_entered if mode == "stop" else entered).wait(), 5)
                await asyncio.sleep(0.05)
                assert service.tasks  # No desktop watchdog terminates the turn.
                await service.stop({"threadId": request["threadId"]})
            await asyncio.wait_for(asyncio.gather(*service.tasks.values()), 20)
            receipt = await service.request_read({"requestId": request["requestId"]})
            assert (
                receipt["state"]
                == {
                    "recover": "completed",
                    "stop": "cancelled",
                    "partial": "cancelled",
                    "unavailable": "failed",
                    "server": "failed",
                }[mode]
            )
            if mode == "recover":
                assert len(attempts) == 66
                assert delays[:5] == [5, 10, 20, 40, 60] and set(delays[4:]) == {60}
            elif mode == "unavailable":
                assert len(attempts) == 1 and not delays
                assert receipt["error"] == "模型调用失败"
                history = await service.read_thread({"threadId": request["threadId"]})
                assert history["messages"][-1]["content"] == "模型调用失败"
                assert history["messages"][-1]["role"] == "assistant"
            elif mode == "server":
                assert len(attempts) == 2  # 503 is not an endless connection retry.
            elif mode == "partial":
                assert released.is_set()
                history = await service.read_thread({"threadId": request["threadId"]})
                assert any(m["content"] == "partial answer" for m in history["messages"])
            health = [
                e["params"]["data"]
                for e in events
                if e.get("method") == "event" and e["params"]["type"] == "model.health"
            ]
            if mode in ("recover", "stop"):
                assert any(h["reason"] == "connection" for h in health)
            assert not health or health[-1]["warning"] is False
        finally:
            await service.close()
            await client.aclose()

    asyncio.run(check())
