import asyncio
import json

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.memory.transcript import render_transcript
from corki.protocol.events import TurnCompleted
from corki.protocol.items import CompactionItem, RemoteHistoryItem
from corki.protocol.tools import ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("automatic", [False, True, "mid"])
@pytest.mark.parametrize("provider", ["openai", "independent"])
def test_ordinary_summary_restores_users_and_archive_without_remote_protocol(
    tmp_path, monkeypatch, automatic, provider
):
    async def scenario():
        requests = []
        routing_headers = []
        executions = []

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            body = json.loads(request.content)
            requests.append((request.url.path, body))
            routing_headers.append(request.headers.get("x-codex-turn-state"))
            assert "client_metadata" not in body and "x-codex-turn-metadata" not in request.headers
            assert not any(
                i.get("type") in {"compaction", "context_compaction", "compaction_trigger"}
                for i in body["input"]
            )
            if len(requests) == 2:
                assert not body.get("tools")
                assert "ORIGINAL_USER" in json.dumps(body["input"])
                assert "OLD_OUTPUT" in json.dumps(body["input"])
            event = {
                "type": "response.completed",
                "response": {
                    "id": f"response-{len(requests)}",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "OLD_OUTPUT"
                                    if len(requests) == 1
                                    else "HARNESS_SUMMARY"
                                    if len(requests) == 2
                                    else "done",
                                }
                            ],
                        }
                    ],
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 2000 if len(requests) == 1 else 1,
                        "total_tokens": 2100 if len(requests) == 1 else 101,
                    },
                },
            }
            if automatic == "mid" and len(requests) == 1:
                event["response"]["output"] = [
                    {
                        "type": "function_call",
                        "name": "guard",
                        "call_id": "actual-tool-call",
                        "arguments": "{}",
                    }
                ]
            return httpx.Response(
                200,
                text=f"data: {json.dumps(event)}\n\n",
                headers={
                    "x-codex-turn-state": "sampling-first" if len(requests) == 1 else "later-state"
                },
            )

        real_client = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: real_client(*a, **kw, transport=httpx.MockTransport(respond)),
        )

        async def create(thread=None):
            class Guard:
                spec = ToolSpec("guard", "guard", {"type": "object"})

                async def execute(self, call, context):
                    executions.append(call.id)
                    return ToolResult(call.id, call.name, "OLD_OUTPUT")

            registry = ToolRegistry()
            registry.register(Guard())
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    tmp_path,
                    api_mode="responses",
                    provider_name=provider,
                    api_key="fixture",
                    api_base="https://fixture.invalid/v1",
                    skills_enabled=False,
                    include_environment_context=False,
                    context_window_tokens=20000,
                    auto_compact_tokens=1800 if automatic else 19000,
                    remote_compaction_v2=False,
                ),
                database_path=tmp_path / "sessions.db",
                registry=registry,
                thread_id=thread,
            )

        runtime = await create()
        thread = runtime.thread_id
        try:
            assert isinstance([e async for e in runtime.stream("ORIGINAL_USER")][-1], TurnCompleted)
            if not automatic:
                assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            assert isinstance([e async for e in runtime.stream("CURRENT_USER")][-1], TurnCompleted)
            visible = requests[-1][1]["input"]
            assert "HARNESS_SUMMARY" in json.dumps(visible)
            assert "CURRENT_USER" in json.dumps(visible)
            assert "ORIGINAL_USER" in json.dumps(visible)
            assert routing_headers == [None] * (4 if automatic == "mid" else 3)
        finally:
            await runtime.aclose()
        cold = await create(thread)
        try:
            assert isinstance([e async for e in cold.stream("CONTINUE")][-1], TurnCompleted)
            assert "HARNESS_SUMMARY" in json.dumps(requests[-1][1]["input"])
            assert "ORIGINAL_USER" in json.dumps(requests[-1][1]["input"])
            assert routing_headers[-1] is None
            stored = await cold._repository.load_items(thread)
            assert not any(isinstance(item, RemoteHistoryItem) for item in stored)
            markers = [item for item in stored if isinstance(item, CompactionItem)]
            assert len(markers) == 1 and markers[0].summary == "HARNESS_SUMMARY"
            assert markers[0].remote_payload_json is None
            transcript = render_transcript(stored, redact=lambda text: text)
            assert "ORIGINAL_USER" in transcript and "OLD_OUTPUT" in transcript
            assert len(requests) == (5 if automatic == "mid" else 4)
            assert executions == (["actual-tool-call"] if automatic == "mid" else [])
        finally:
            await cold.aclose()

    asyncio.run(scenario())
