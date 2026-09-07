"""Stage-specific models/effort reach both HTTP adapters without mutating main defaults."""

import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import OpenAICompatibleModel, OpenAIResponsesModel, resolve_capabilities
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import UserMessageItem
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("case", ["default", "provider", "explicit", "priority", "unavailable"])
def test_memory_models_and_effort_reach_http_and_failures_stay_background(tmp_path, mode, case):
    async def scenario():
        database, root = tmp_path / "sessions.db", tmp_path / "memories"
        sessions = SQLiteSessionRepository(database)
        source = new_thread_id()
        await sessions.create_thread(source, tmp_path)
        await sessions.append_items(
            source, (UserMessageItem("Use pytest. " + "x" * 30000, new_turn_id()),)
        )
        provider = (
            'memory_extraction_model="provider-small"\nmemory_consolidation_model="provider-large"\n'
            if case in {"provider", "priority"}
            else ""
        )
        stage = (
            'extraction_model="chosen-small"\nconsolidation_model="chosen-large"\n'
            if case in {"explicit", "priority"}
            else ""
        )
        extract, consolidate = (
            ("chosen-small", "chosen-large")
            if stage
            else ("provider-small", "provider-large")
            if provider
            else ("gpt-5.6-luna", "gpt-5.6-terra")
        )
        config = tmp_path / "config.toml"
        config.write_text(
            f'[agent]\nmodel="main"\n[provider]\napi_mode="{mode}"\nreasoning_effort="high"\n{provider}[skills]\nenabled=false\n[memories]\nenabled=true\nmin_thread_idle_hours=0\n{stage}[models.catalog."{extract}"]\ncontext_window=4000\neffective_context_window_percent=80\n'
        )
        settings = CorkiSettings.for_directory(tmp_path, config_file=config)
        payloads = []

        def respond(request):
            payload = json.loads(request.content)
            payloads.append(payload)
            schema = (
                payload.get("response_format", {}).get("json_schema", {})
                if mode == "chat_completions"
                else payload.get("text", {}).get("format", {})
            )
            name = schema.get("name")
            effort = (
                payload.get("reasoning_effort")
                if mode == "chat_completions"
                else payload.get("reasoning", {}).get("effort")
            )
            if name == "corki_memory_extraction":
                assert payload["model"] == extract and effort == "low"
                assert "tokens truncated" in json.dumps(payload, ensure_ascii=False)
                if case == "unavailable":
                    return httpx.Response(
                        400,
                        json={"error": {"message": "model unavailable", "code": "model_not_found"}},
                    )
                value = {
                    "raw_memory": "Use pytest.",
                    "rollout_summary": "pytest workflow",
                    "rollout_slug": None,
                }
                content = json.dumps(value)
            elif name == "corki_memory_consolidation":
                assert payload["model"] == consolidate and effort == "medium"
                content = json.dumps(
                    {"memory": "Use pytest.", "memory_summary": "pytest route", "skills": []}
                )
            else:
                assert payload["model"] == "main" and effort == "high"
                content = "ready"
            if mode == "chat_completions":
                packet = {
                    "choices": [
                        {"index": 0, "delta": {"content": content}, "finish_reason": "stop"}
                    ]
                }
                body = f"data: {json.dumps(packet)}\n\ndata: [DONE]\n\n"
            else:
                packet = {
                    "type": "response.completed",
                    "response": {
                        "id": "response",
                        "status": "completed",
                        "output": [
                            {
                                "type": "message",
                                "id": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": content}],
                            }
                        ],
                    },
                }
                body = f"data: {json.dumps(packet)}\n\n"
            return httpx.Response(200, text=body)

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        adapter = OpenAICompatibleModel if mode == "chat_completions" else OpenAIResponsesModel
        model = adapter(
            api_key="test",
            base_url=settings.api_base,
            capabilities=resolve_capabilities(base_url=settings.api_base, api_mode=mode),
            reasoning_effort="high",
            max_retries=0,
            request_max_retries=0,
            client=client,
        )
        runtime = LangGraphRuntime.create(
            settings=settings,
            database_path=database,
            model=model,
            memory_model=model,
            memory_root=root,
            home_path=tmp_path / "home",
        )
        try:
            assert isinstance([e async for e in runtime.stream("first")][-1], TurnCompleted)
            report = await runtime._memory_service.wait()
            assert report.failed == (case == "unavailable"), runtime._memory_service.warnings
            assert report.extracted == (case != "unavailable") and report.consolidated
            assert isinstance([e async for e in runtime.stream("after memory")][-1], TurnCompleted)
            assert len(payloads) == 4
            assert [p["model"] for p in payloads].count("main") == 2
            assert (root / "MEMORY.md").exists()
        finally:
            await runtime.aclose()
            await client.aclose()
            await sessions.close()

    asyncio.run(scenario())
