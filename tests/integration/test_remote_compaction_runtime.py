import asyncio
import json

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.events import TurnCompleted
from corki.protocol.items import CompactionItem
from corki.protocol.tools import ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("automatic", [False, True, "mid"])
def test_summary_tool_call_is_not_executed_and_cold_history_stays_ordinary(
    tmp_path, monkeypatch, automatic
):
    async def scenario():
        requests = []
        routing_headers = []
        executions = []

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            body = json.loads(request.content)
            requests.append(body)
            routing_headers.append(request.headers.get("x-codex-turn-state"))
            compact = len(requests) == 2
            assert not any(
                i.get("type") in {"compaction_trigger", "compaction", "context_compaction"}
                for i in body["input"]
            )
            events = []
            if compact:
                assert not body.get("tools")
                assert "client_metadata" not in body
                assert "x-codex-turn-metadata" not in request.headers
                events.extend(
                    [
                        {
                            "type": "response.output_item.done",
                            "item": {
                                "type": "function_call",
                                "name": "guard",
                                "call_id": "never-execute",
                                "arguments": "{}",
                            },
                        },
                    ]
                )
            output = [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "SAFE_SUMMARY"
                            if compact
                            else "OLD_OUTPUT " * 300
                            if len(requests) == 1
                            else "done",
                        }
                    ],
                }
            ]
            if compact:
                output.insert(0, events[-1]["item"])
            events.append(
                {
                    "type": "response.completed",
                    "response": {
                        "id": f"r-{len(requests)}",
                        "output": output,
                        "usage": {
                            "input_tokens": 100,
                            "output_tokens": 2000 if len(requests) == 1 else 1,
                            "total_tokens": 2100 if len(requests) == 1 else 101,
                        },
                    },
                }
            )
            if len(requests) == 1 and automatic == "mid":
                events[-1]["response"]["output"] = [
                    {
                        "type": "function_call",
                        "name": "guard",
                        "arguments": "{}",
                        "call_id": "normal-tool-call",
                        "id": "tool-first",
                    }
                ]
            return httpx.Response(
                200,
                text="".join(f"data: {json.dumps(e)}\n\n" for e in events),
                headers={
                    "x-codex-turn-state": "compact-seed"
                    if compact
                    else "sampling-first"
                    if len(requests) == 1
                    else "later-state"
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
                    return ToolResult(call.id, call.name, "OLD_OUTPUT " * 300)

            registry = ToolRegistry()
            registry.register(Guard())
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    tmp_path,
                    provider_name="openai",
                    api_mode="responses",
                    api_key="fixture",
                    api_base="https://fixture.invalid/v1",
                    skills_enabled=False,
                    include_environment_context=False,
                    context_window_tokens=20000,
                    auto_compact_tokens=1800 if automatic else 19000,
                    remote_compaction_v2=True,  # Retired configuration remains inert.
                ),
                database_path=tmp_path / "sessions.db",
                registry=registry,
                thread_id=thread,
                session_id="different-new-default" if thread else "stored-remote-session",
            )

        runtime = await create()
        thread = runtime.thread_id
        try:
            assert isinstance([e async for e in runtime.stream("ORIGINAL_USER")][-1], TurnCompleted)
            if not automatic:
                assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            assert isinstance([e async for e in runtime.stream("CURRENT_USER")][-1], TurnCompleted)
            assert "SAFE_SUMMARY" in json.dumps(requests[-1]["input"])
            assert "never-execute" not in json.dumps(requests[-1])
            assert "OLD_OUTPUT" not in json.dumps(requests[-1])
            assert "CURRENT_USER" in json.dumps(requests[-1])
            assert routing_headers == [None] * (4 if automatic == "mid" else 3)
        finally:
            await runtime.aclose()
        cold = await create(thread)
        try:
            assert isinstance([e async for e in cold.stream("CONTINUE")][-1], TurnCompleted)
            assert "SAFE_SUMMARY" in json.dumps(requests[-1]["input"])
            assert "ORIGINAL_USER" in json.dumps(requests[-1]["input"])
            assert routing_headers[-1] is None
            stored = await cold._repository.load_items(thread)
            markers = [item for item in stored if isinstance(item, CompactionItem)]
            assert len(markers) == 1 and markers[0].summary == "SAFE_SUMMARY"
            assert markers[0].remote_payload_json is None
            assert len(requests) == (5 if automatic == "mid" else 4)
            assert "OLD_OUTPUT" in str(stored)
            assert not any(i.get("type") == "compaction_trigger" for i in requests[-1]["input"])
            assert executions == (["normal-tool-call"] if automatic == "mid" else [])
        finally:
            await cold.aclose()

    asyncio.run(scenario())
