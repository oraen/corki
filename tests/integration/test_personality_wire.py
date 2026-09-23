"""Personality is host-owned ordinary message content, never a provider extension."""

import asyncio
import json

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.config.model_context import parse_model_contexts
from corki.core import LangGraphRuntime
from corki.protocol.events import TurnCompleted
from corki.tools import ToolRegistry


@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("provider", ["openai", "independent"])
def test_personality_ordinary_wire_cold_changes_and_compaction(
    tmp_path, monkeypatch, api_mode, provider
):
    async def scenario():
        bodies = []

        def respond(request):
            assert request.url.host == "fixture.invalid"
            assert request.url.path == (
                "/v1/responses" if api_mode == "responses" else "/v1/chat/completions"
            )
            assert request.headers["authorization"] == "Bearer fixture"
            assert not any(k.startswith("x-codex") for k in request.headers)
            body = json.loads(request.content)
            bodies.append(body)
            assert not {
                "personality",
                "namespace",
                "context_management",
                "previous_response_id",
            }.intersection(body)
            assert all(t.get("type") == "function" for t in body.get("tools", []))
            if api_mode == "responses":
                packet = {
                    "type": "response.completed",
                    "response": {
                        "id": f"r-{len(bodies)}",
                        "output": [
                            {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": "done"}],
                            }
                        ],
                    },
                }
            else:
                packet = {"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]}
            return httpx.Response(200, text=f"data: {json.dumps(packet)}\n\n")

        client = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: client(*a, **kw, transport=httpx.MockTransport(respond)),
        )
        catalog = parse_model_contexts(
            {
                name: {
                    "context_window": 200_000,
                    "model_messages": {
                        "instructions_template": "BASE {{ personality }}",
                        "instructions_variables": {
                            "personality_default": "DEFAULT",
                            "personality_friendly": "FRIENDLY",
                            "personality_pragmatic": "PRAGMATIC",
                        },
                    },
                }
                for name in ("large", "small")
            }
        )
        thread, prefix = None, ()
        choices = (
            ("large", "friendly"),
            ("large", "pragmatic"),
            ("large", "pragmatic"),
            ("small", "pragmatic"),
        )
        for index, (model, personality) in enumerate(choices):
            runtime = await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    tmp_path,
                    model=model,
                    model_contexts=catalog,
                    personality=personality,
                    skills_enabled=False,
                    plugins_enabled=False,
                    include_environment_context=False,
                    api_mode=api_mode,
                    provider_name=provider,
                    api_key="fixture",
                    api_base="https://fixture.invalid/v1",
                    base_instructions="HOST BASE",
                ),
                database_path=tmp_path / "history.db",
                home_path=tmp_path / "home",
                registry=ToolRegistry(),
                thread_id=thread,
            )
            try:
                assert isinstance([e async for e in runtime.stream("go")][-1], TurnCompleted)
                body = bodies[-1]
                messages = body["input" if api_mode == "responses" else "messages"]
                selected = [m for m in messages if "<personality_spec>" in json.dumps(m)]
                assert len(selected) == (1 if index == 0 else 2)
                assert all(
                    m["role"] == ("developer" if api_mode == "responses" else "system")
                    for m in selected
                )
                assert ("FRIENDLY" if index == 0 else "PRAGMATIC") in json.dumps(selected[-1])
                assert "personality.spec_instructions" not in json.dumps(body)
                if index == 3:
                    switches = [m for m in messages if "<model_switch>" in json.dumps(m)]
                    assert len(switches) == 1 and "PRAGMATIC" in json.dumps(switches[0])
                    assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
                    assert isinstance([e async for e in runtime.stream("after")][-1], TurnCompleted)
                    messages = bodies[-1]["input" if api_mode == "responses" else "messages"]
                    selected = [m for m in messages if "<personality_spec>" in json.dumps(m)]
                    assert len(selected) == 1 and "PRAGMATIC" in json.dumps(selected[0])
                stored = await runtime._repository.load_items(runtime.thread_id)
                assert stored[: len(prefix)] == prefix
                prefix, thread = stored, runtime.thread_id
            finally:
                await runtime.aclose()
        assert len(bodies) == 6

    asyncio.run(scenario())
