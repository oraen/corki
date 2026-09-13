import asyncio
import json

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.items import CompactionItem
from corki.tools import ToolRegistry


def settings(tmp_path):
    return CorkiSettings(
        tmp_path,
        api_mode="responses",
        provider_name="openai",
        api_key="fixture",
        api_base="https://fixture.invalid/v1",
        skills_enabled=False,
        model_max_retries=1,
        model_request_max_retries=0,
        model_retry_base_seconds=0.001,
    )


def packet(events):
    return httpx.Response(200, text="".join(f"data: {json.dumps(e)}\n\n" for e in events))


DONE = {
    "type": "response.output_item.done",
    "output_index": 0,
    "item": {
        "type": "message",
        "role": "assistant",
        "id": "msg_summary",
        "content": [{"type": "output_text", "text": "ordinary summary"}],
    },
}
COMPLETED = {"type": "response.completed", "response": {"id": "r"}}


@pytest.mark.parametrize(
    "failure,attempts",
    [
        ("missing_output", 2),
        ("conflicting_done", 2),
        ("malformed", 2),
        ("missing_terminal", 2),
        ("failed_without_completed", 2),
        ("auth", 2),
    ],
)
def test_invalid_summary_never_installs_partial_history(tmp_path, monkeypatch, failure, attempts):
    async def scenario():
        payloads = []

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            payloads.append(json.loads(request.content))
            if failure == "auth":
                return httpx.Response(401)
            events = {
                "missing_output": [COMPLETED],
                "conflicting_done": [
                    DONE,
                    {
                        **DONE,
                        "item": {
                            **DONE["item"],
                            "content": [{"type": "output_text", "text": "changed summary"}],
                        },
                    },
                    COMPLETED,
                ],
                "malformed": [
                    {
                        "type": "response.output_item.done",
                        "item": {**DONE["item"], "content": 5},
                    },
                    COMPLETED,
                ],
                "missing_terminal": [DONE],
                "failed_without_completed": [
                    {
                        "type": "response.failed",
                        "response": {"error": {"code": "invalid_prompt", "message": "invalid"}},
                    },
                    DONE,
                ],
            }[failure]
            return packet(events)

        real = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: real(*a, **kw, transport=httpx.MockTransport(respond)),
        )
        runtime = LangGraphRuntime.create(
            settings=settings(tmp_path), database_path=tmp_path / "s.db", registry=ToolRegistry()
        )
        try:
            events = [e async for e in runtime.compact()]
            assert isinstance(events[-1], TurnFailed)
            assert len(payloads) == attempts
            assert all(not p.get("tools") for p in payloads)
            assert all(
                not any(i.get("type") == "compaction_trigger" for i in p["input"]) for p in payloads
            )
            assert all(p == payloads[0] for p in payloads)
            assert not any(
                isinstance(i, CompactionItem)
                for i in await runtime._repository.load_items(runtime.thread_id)
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("close", [False, True])
def test_partial_summary_cancel_closes_http_without_commit(tmp_path, monkeypatch, close):
    async def scenario():
        entered, cleaned = asyncio.Event(), asyncio.Event()

        class Slow(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield f"data: {json.dumps(DONE)}\n\n".encode()
                entered.set()
                await asyncio.Event().wait()

            async def aclose(self):
                cleaned.set()

        real = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: real(
                *a,
                **kw,
                transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Slow())),
            ),
        )
        runtime = LangGraphRuntime.create(
            settings=settings(tmp_path), database_path=tmp_path / "s.db", registry=ToolRegistry()
        )

        async def consume():
            return [e async for e in runtime.compact()]

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            await (runtime.aclose() if close else runtime.cancel_active())
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert cleaned.is_set()
            assert not any(
                isinstance(i, CompactionItem)
                for i in await runtime._repository.load_items(runtime.thread_id)
            )
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_compaction_retries_same_request_after_partial_output(tmp_path, monkeypatch):
    async def scenario():
        payloads = []
        routing_headers = []

        def respond(request):
            payloads.append(json.loads(request.content))
            routing_headers.append(request.headers.get("x-codex-turn-state"))
            response = packet([DONE] if len(payloads) == 1 else [DONE, COMPLETED])
            response.headers["x-codex-turn-state"] = "compact-first"
            return response

        real = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: real(*a, **kw, transport=httpx.MockTransport(respond)),
        )
        runtime = LangGraphRuntime.create(
            settings=settings(tmp_path), database_path=tmp_path / "s.db", registry=ToolRegistry()
        )
        try:
            assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            assert len(payloads) == 2 and payloads[0] == payloads[1]
            assert routing_headers == [None, None]
            assert (
                len(
                    [
                        i
                        for i in await runtime._repository.load_items(runtime.thread_id)
                        if isinstance(i, CompactionItem)
                    ]
                )
                == 1
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
