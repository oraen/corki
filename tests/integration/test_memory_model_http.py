"""Stage-specific models/effort reach both HTTP adapters without mutating main defaults."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.config.model_context import parse_model_contexts
from corki.core import LangGraphRuntime
from corki.models import OpenAICompatibleModel, OpenAIResponsesModel, resolve_capabilities
from corki.models.capabilities import StructuredOutputProtocol
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import UserMessageItem
from corki.storage import SQLiteSessionRepository


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("provider_name", ["openai", "independent"])
@pytest.mark.parametrize("case", ["default", "provider", "explicit", "priority", "unavailable"])
def test_memory_models_and_effort_reach_http_and_failures_stay_background(
    tmp_path, mode, provider_name, case
):
    async def scenario():
        database, root = tmp_path / "sessions.db", tmp_path / "memories"
        sessions = SQLiteSessionRepository(database)
        source = new_thread_id()
        await sessions.create_thread(source, tmp_path)
        await sessions.append_items(
            source, (UserMessageItem("Use pytest. " + "x" * 300000, new_turn_id()),)
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
            else ("main", "main")
        )
        config = tmp_path / "config.toml"
        config.write_text(
            f'[agent]\nmodel="main"\n[provider]\napi_mode="{mode}"\nreasoning_effort="high"\n{provider}[skills]\nenabled=false\n[memories]\nenabled=true\nmin_thread_idle_hours=0\n{stage}[models.catalog."{extract}"]\ncontext_window=4000\neffective_context_window_percent=80\n'
        )
        settings = CorkiSettings.for_directory(tmp_path, config_file=config)
        settings = replace(
            settings,
            api_base="https://fixture.invalid/v1",
            content_item_kinds=case != "explicit",
            service_tier="priority",
            model_contexts=parse_model_contexts(
                {
                    extract: {
                        "context_window": 4000,
                        "effective_context_window_percent": 80,
                        "default_reasoning_level": "xhigh",
                        "default_reasoning_summary": "detailed",
                        "use_responses_lite": True,
                    },
                    consolidate: {
                        "default_reasoning_level": "xhigh",
                        "default_reasoning_summary": "none",
                        "use_responses_lite": case in {"default", "priority"},
                        "service_tiers": [{"id": "priority"}],
                    },
                    "main": {
                        "context_window": 64000,
                        "effective_context_window_percent": 80,
                        "default_reasoning_summary": "concise",
                        "service_tiers": [{"id": "priority"}],
                    },
                }
            ),
        )
        payloads, fixture_errors = [], []

        def respond(request):
            endpoint = "chat/completions" if mode == "chat_completions" else "responses"
            assert str(request.url) == f"https://fixture.invalid/v1/{endpoint}"
            payload = json.loads(request.content)
            assert request.headers.get("x-codex-turn-state") is None
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
            assert payload.get("service_tier") == (
                None if effort == "low" and extract != "main" else "priority"
            )
            if mode == "responses":
                assert "x-openai-internal-codex-responses-lite" not in request.headers
                assert "context" not in payload.get("reasoning", {})
                assert "instructions" in payload
                assert all(i.get("type") != "additional_tools" for i in payload["input"])
                expected_summary = (
                    "concise"
                    if payload["model"] == "main"
                    else "detailed"
                    if payload["model"] == extract
                    else None
                    if payload["model"] == consolidate
                    else "concise"
                )
                assert payload["reasoning"].get("summary") == expected_summary
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
            elif effort == "medium":
                assert payload["model"] == consolidate and effort == "medium"
                tools = payload["tools"]
                assert not schema and tools
                content = json.dumps(
                    {"memory": "Use pytest.", "memory_summary": "pytest route", "skills": []}
                )
            else:
                assert payload["model"] == "main" and effort == "high"
                content = "ready"
            if mode == "chat_completions":
                assert request.headers.get("x-openai-internal-codex-responses-lite") is None
                assert "additional_tools" not in json.dumps(payload)
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
            return httpx.Response(
                200, text=body, headers={"x-codex-turn-state": f"state-{payload['model']}"}
            )

        def checked_respond(request):
            try:
                return respond(request)
            except AssertionError as error:
                fixture_errors.append(str(error))
                raise

        client = httpx.AsyncClient(transport=httpx.MockTransport(checked_respond))
        adapter = OpenAICompatibleModel if mode == "chat_completions" else OpenAIResponsesModel
        model = adapter(
            api_key="test",
            base_url=settings.api_base,
            capabilities=replace(
                resolve_capabilities(
                    base_url=settings.api_base, api_mode=mode, provider_name=provider_name
                ),
                structured_output_protocol=StructuredOutputProtocol.JSON_SCHEMA,
                supports_reasoning_effort=True,
                supports_service_tier=True,
            ),
            reasoning_effort="high",
            max_retries=0,
            request_max_retries=0,
            client=client,
        )
        runtime = await LangGraphRuntime.acreate(
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
            assert not fixture_errors
            assert report.failed == (case == "unavailable"), runtime._memory_service.warnings
            assert report.extracted == (case != "unavailable") and report.consolidated
            assert isinstance([e async for e in runtime.stream("after memory")][-1], TurnCompleted)
            assert len(payloads) == 4
            assert [p["model"] for p in payloads].count("main") == (
                4 if extract == consolidate == "main" else 2
            )
            assert (root / "MEMORY.md").exists()
        finally:
            await runtime.aclose()
            await client.aclose()
            await sessions.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("provider", ["openai", "deepseek", "independent"])
def test_runtime_owned_memory_clients_use_ordinary_configured_transport(
    tmp_path, monkeypatch, mode, provider
):
    async def scenario():
        database, root = tmp_path / "owned.db", tmp_path / "memories"
        sessions = SQLiteSessionRepository(database)
        source = new_thread_id()
        await sessions.create_thread(source, tmp_path)
        original = UserMessageItem("Use pytest. " + "x" * 300000, new_turn_id())
        await sessions.append_items(source, (original,))
        requests = []

        def respond(request):
            endpoint = "responses" if mode == "responses" else "chat/completions"
            assert str(request.url) == f"https://fixture.invalid/v1/{endpoint}"
            assert request.headers["authorization"] == "Bearer fixture"
            body = json.loads(request.content)
            requests.append(body)
            assert "thinking" not in body
            assert "x-openai-internal-codex-responses-lite" not in request.headers
            if mode == "chat_completions":
                assert "response_format" not in body and "reasoning_effort" not in body
            if body["model"] == "extract":
                text = json.dumps(
                    {
                        "raw_memory": "Use pytest.",
                        "rollout_summary": "pytest workflow",
                        "rollout_slug": None,
                    }
                )
            elif body["model"] == "merge":
                assert body.get("tools")
                text = json.dumps(
                    {"memory": "Use pytest.", "memory_summary": "pytest route", "skills": []}
                )
            else:
                assert body["model"] == "main"
                text = "ready"
            packet = (
                {
                    "type": "response.completed",
                    "response": {
                        "id": "r",
                        "output": [
                            {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": text}],
                            }
                        ],
                    },
                }
                if mode == "responses"
                else {"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]}
            )
            return httpx.Response(
                200,
                text="data: "
                + json.dumps(packet)
                + "\n\n"
                + ("data: [DONE]\n\n" if mode != "responses" else ""),
            )

        client = httpx.AsyncClient
        clients = []

        def owned_client(*args, **kwargs):
            instance = client(*args, **kwargs, transport=httpx.MockTransport(respond))
            clients.append(instance)
            return instance

        monkeypatch.setattr(http_client, "OwnedHTTPClient", owned_client)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                model="main",
                api_mode=mode,
                provider_name=provider,
                api_base="https://fixture.invalid/v1",
                api_key="fixture",
                skills_enabled=False,
                thinking_enabled=True,
                memories_enabled=True,
                memories_min_thread_idle_hours=0,
                memories_extraction_model="extract",
                memories_consolidation_model="merge",
                model_request_max_retries=0,
                model_max_retries=0,
            ),
            database_path=database,
            memory_root=root,
            home_path=tmp_path / "home",
        )
        try:
            assert isinstance([e async for e in runtime.stream("first")][-1], TurnCompleted)
            report = await runtime._memory_service.wait()
            assert report.extracted == 1 and report.consolidated and not report.failed
            prefix = await runtime._repository.load_items(runtime.thread_id)
            assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            assert isinstance([e async for e in runtime.stream("after memory")][-1], TurnCompleted)
            assert (await runtime._repository.load_items(runtime.thread_id))[
                : len(prefix)
            ] == prefix
            assert sorted(body["model"] for body in requests) == [
                "extract",
                "main",
                "main",
                "main",
                "merge",
            ]
            assert "pytest route" in json.dumps(requests[-1])
            assert "Use pytest." in (root / "MEMORY.md").read_text()
            assert await sessions.load_items(source) == (original,)
        finally:
            await runtime.aclose()
            await sessions.close()
        assert len(clients) >= 2 and all(client.is_closed for client in clients)

    asyncio.run(scenario())
