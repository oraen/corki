"""All provider labels and legacy compaction flags use ordinary model summaries."""

import asyncio
import json

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.events import TurnCompleted
from corki.protocol.items import CompactionItem
from corki.tools import ToolRegistry


@pytest.mark.parametrize("provider", ["openai", "independent"])
@pytest.mark.parametrize("base", ["https://api.openai.com/v1", "https://fixture.invalid/v1"])
@pytest.mark.parametrize("legacy_v2", [True, False])
@pytest.mark.parametrize("automatic", [True, False])
@pytest.mark.parametrize("token_budget", [False, True])
def test_summary_transport_is_provider_independent(
    tmp_path, monkeypatch, provider, base, legacy_v2, automatic, token_budget
):
    async def scenario():
        requests, summaries = [], []

        def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            assert str(request.url) == base + "/responses"
            assert not any(i.get("type") == "compaction_trigger" for i in body["input"])
            summary = any(
                part.get("text") == "HARNESS_SUMMARY_REQUEST"
                for item in body["input"]
                for part in item.get("content", [])
                if isinstance(part, dict)
            )
            if summary:
                summaries.append(body)
                assert not body.get("tools")
            event = {
                "type": "response.completed",
                "response": {
                    "id": "r",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": "SUMMARY_PROOF" if summary else "done",
                                }
                            ],
                        }
                    ],
                    "usage": {
                        "input_tokens": 25000 if len(requests) == 1 else 10,
                        "output_tokens": 1,
                        "total_tokens": 25001 if len(requests) == 1 else 11,
                    },
                },
            }
            return httpx.Response(200, text="data: " + json.dumps(event) + "\n\n")

        client_type = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: client_type(*a, **kw, transport=httpx.MockTransport(respond)),
        )

        def create(thread=None):
            return LangGraphRuntime.create(
                settings=CorkiSettings(
                    tmp_path,
                    api_mode="responses",
                    api_base=base,
                    api_key="fixture",
                    provider_name=provider,
                    skills_enabled=False,
                    model="fixture",
                    context_window_tokens=50000,
                    auto_compact_tokens=20000,
                    compact_prompt="HARNESS_SUMMARY_REQUEST",
                    remote_compaction_v2=legacy_v2,
                    token_budget_enabled=token_budget,
                    model_max_retries=0,
                    model_request_max_retries=0,
                ),
                database_path=tmp_path / "history.db",
                registry=ToolRegistry(),
                thread_id=thread,
            )

        runtime = create()
        try:
            assert isinstance([e async for e in runtime.stream("ORIGINAL")][-1], TurnCompleted)
            if not automatic:
                assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            assert isinstance([e async for e in runtime.stream("CURRENT")][-1], TurnCompleted)
            assert len(summaries) == 1
            assert "CURRENT" not in json.dumps(summaries[0])
            stored = await runtime._repository.load_items(runtime.thread_id)
            markers = [i for i in stored if isinstance(i, CompactionItem)]
            assert len(markers) == 1 and markers[0].summary == "SUMMARY_PROOF"
            assert markers[0].remote_payload_json is None
            thread = runtime.thread_id
            await runtime.aclose()
            runtime = create(thread)
            assert isinstance([e async for e in runtime.stream("COLD")][-1], TurnCompleted)
            assert len(summaries) == 1
            assert "SUMMARY_PROOF" in json.dumps(requests[-1])
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
