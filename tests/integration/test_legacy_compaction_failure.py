import asyncio
import json

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.events import ModelRetryScheduled, TurnCompleted, TurnFailed
from corki.protocol.items import CompactionItem, TurnAbortedItem, UserMessageItem
from corki.tools import ToolRegistry


def settings(tmp_path):
    return CorkiSettings(
        tmp_path,
        api_mode="responses",
        provider_name="openai",
        api_key="fixture",
        api_base="https://fixture.invalid/v1",
        skills_enabled=False,
        remote_compaction_v2=False,
        model_max_retries=1,
        model_request_max_retries=2,
        model_retry_base_seconds=0.001,
    )


@pytest.mark.parametrize(
    "failure,attempts",
    [
        ("missing_output", 2),
        ("malformed_summary", 2),
        ("partial_json", 2),
        ("auth", 2),
        ("rate_limit", 2),
        ("server", 6),
        ("body_read", 2),
        ("empty", 2),
        ("malformed_discarded_tool", 2),
        ("malformed_developer", 2),
    ],
)
@pytest.mark.parametrize("recover", [False, True])
def test_ordinary_summary_failure_has_bounded_retries_and_atomic_history(
    tmp_path, monkeypatch, failure, attempts, recover
):
    async def scenario():
        requests = []
        closed = []
        body_failures = []

        class Broken(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "type": "response.output_item.done",
                            "output_index": 0,
                            "item": {
                                "type": "message",
                                "id": "msg_partial",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "partial summary"}],
                            },
                        }
                    )
                    + "\n\n"
                ).encode()
                body_failures.append(True)
                raise httpx.ReadError("broken body")

            async def aclose(self):
                closed.append(True)

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            requests.append(json.loads(request.content))
            assert not requests[-1].get("tools")
            if recover and len(requests) > 1:
                return httpx.Response(
                    200,
                    text="data: "
                    + json.dumps(
                        {
                            "type": "response.completed",
                            "response": {
                                "id": "recovered",
                                "output": [
                                    {
                                        "type": "message",
                                        "role": "assistant",
                                        "id": "msg_summary",
                                        "content": [
                                            {
                                                "type": "output_text",
                                                "text": "complete recovered summary",
                                            }
                                        ],
                                    }
                                ],
                            },
                        }
                    )
                    + "\n\n",
                )
            if failure in ("auth", "rate_limit", "server"):
                return httpx.Response({"auth": 401, "rate_limit": 429, "server": 503}[failure])
            if failure == "body_read":
                return httpx.Response(200, stream=Broken())
            if failure == "partial_json":
                return httpx.Response(200, content=b'data: {"type":"response.completed",\n\n')
            return httpx.Response(
                200,
                text="data: "
                + json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "missing_output": {},
                            "malformed_summary": {
                                "output": [{"type": "message", "role": "assistant", "content": 42}]
                            },
                            "empty": {"output": []},
                            "malformed_discarded_tool": {
                                "output": [
                                    {
                                        "type": "function_call",
                                        "name": "tool",
                                        "call_id": "c",
                                        "arguments": {},
                                    }
                                ]
                            },
                            "malformed_developer": {
                                "output": [
                                    {"type": "message", "role": "developer", "content": "invalid"}
                                ]
                            },
                        }[failure],
                    }
                )
                + "\n\n",
            )

        real = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: real(*a, **kw, transport=httpx.MockTransport(respond)),
        )
        runtime = await LangGraphRuntime.acreate(
            settings=settings(tmp_path), database_path=tmp_path / "s.db", registry=ToolRegistry()
        )
        try:
            await runtime._ensure_ready()
            old = UserMessageItem("original must remain in archive", "old")
            await runtime._repository.append_items(runtime.thread_id, (old,))
            events = [event async for event in runtime.compact()]
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert isinstance(events[-1], TurnCompleted if recover else TurnFailed)
            if recover:
                assert stored[0] == old
                markers = [item for item in stored if isinstance(item, CompactionItem)]
                assert len(markers) == 1 and markers[0].summary == "complete recovered summary"
            else:
                assert stored == (old,)
            expected = 2 if recover else attempts
            assert len(requests) == expected and all(r == requests[0] for r in requests)
            retries = [event for event in events if isinstance(event, ModelRetryScheduled)]
            assert len(retries) == (0 if recover and failure == "server" else 1)
            assert all(event.purpose == "compaction" for event in retries)
            if failure == "body_read":
                assert len(closed) == len(body_failures) == (1 if recover else attempts)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("close", [False, True])
def test_ordinary_summary_cancel_closes_body_and_preserves_window(tmp_path, monkeypatch, close):
    async def scenario():
        entered, cleaned = asyncio.Event(), asyncio.Event()

        class Slow(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'
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
        runtime = await LangGraphRuntime.acreate(
            settings=settings(tmp_path), database_path=tmp_path / "s.db", registry=ToolRegistry()
        )
        await runtime._ensure_ready()
        old = UserMessageItem("old", "old-turn")
        await runtime._repository.append_items(runtime.thread_id, (old,))

        async def consume():
            return [event async for event in runtime.compact()]

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            await (runtime.aclose() if close else runtime.cancel_active())
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert cleaned.is_set()
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert len(stored) == 2 and stored[0] == old
            assert isinstance(stored[1], TurnAbortedItem)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())
