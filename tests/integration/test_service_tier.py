import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.events import TurnCompleted, WarningEvent
from corki.protocol.items import CompactionItem
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["responses", "chat_completions", "legacy", "local"])
@pytest.mark.parametrize(
    "selected,fast,supported,expected,warn",
    [
        (None, True, True, None, False),
        ("fast", True, True, "priority", False),
        ("priority", True, True, "priority", False),
        ("flex", True, True, "flex", False),
        ("ultrafast", True, True, None, True),
        ("default", True, True, None, False),
        ("priority", False, True, None, False),
        ("fast", False, True, None, False),
        ("priority", True, False, None, False),
    ],
)
def test_service_tier_selection_warning_and_compaction_paths(
    tmp_path, monkeypatch, mode, selected, fast, supported, expected, warn
):
    async def scenario():
        bodies = []

        def respond(request):
            endpoint = "chat/completions" if mode == "chat_completions" else "responses"
            assert str(request.url) == f"https://fixture.invalid/v1/{endpoint}"
            bodies.append(json.loads(request.content))
            if mode == "chat_completions":
                packet = {
                    "choices": [
                        {"index": 0, "delta": {"content": "summary"}, "finish_reason": "stop"}
                    ]
                }
                return httpx.Response(
                    200, text="data: " + json.dumps(packet) + "\n\ndata: [DONE]\n\n"
                )
            events = []
            events.append(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "r",
                        "output": [
                            {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "summary"}],
                            }
                        ],
                    },
                }
            )
            return httpx.Response(
                200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events)
            )

        client = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: client(*a, **kw, transport=httpx.MockTransport(respond)),
        )
        config = tmp_path / "config.toml"
        api_mode = "chat_completions" if mode == "chat_completions" else "responses"
        config.write_text(
            '[agent]\nmodel="main"\n[skills]\nenabled=false\n[provider]\napi_key="fixture"\nbase_url="https://fixture.invalid/v1"\n'
            + f'api_mode="{api_mode}"\nname="{"custom" if mode == "local" else "openai"}"\n'
            + (f'service_tier="{selected}"\n' if selected is not None else "")
            + f"supports_service_tier={str(supported).lower()}\n[features]\n"
            + f"fast_mode={str(fast).lower()}\n"
            + '[models.catalog.main]\ncontext_window=100000\ndefault_service_tier="priority"\n'
            + 'service_tiers=[{id="priority"}, {id="flex"}]\n'
        )
        settings = replace(
            CorkiSettings.for_directory(tmp_path, config_file=config),
            remote_compaction_v2=mode != "legacy",
        )
        runtime = await LangGraphRuntime.acreate(
            settings=settings, database_path=tmp_path / "s.db", registry=ToolRegistry()
        )
        try:
            first = [e async for e in runtime.stream("first")]
            assert isinstance(first[-1], TurnCompleted)
            assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            following = [e async for e in runtime.stream("following")]
            assert isinstance(following[-1], TurnCompleted)
            assert len(bodies) == 3
            assert [b.get("service_tier") for b in bodies] == [expected] * 3
            stored = await runtime._repository.load_items(runtime.thread_id)
            summaries = [item for item in stored if isinstance(item, CompactionItem)]
            assert len(summaries) == 1
            assert summaries[0].summary == "summary"
            assert summaries[0].remote_payload_json is None
            warnings = [
                e.message
                for e in first
                if isinstance(e, WarningEvent) and "service tier" in e.message
            ]
            assert bool(warnings) == warn
            if warn:
                assert selected in warnings[0] and "main" in warnings[0]
                assert warnings[0] not in json.dumps(bodies)
            assert not any(
                isinstance(e, WarningEvent) and "service tier" in e.message for e in following
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
