"""Rejected non-summary responses cannot replace Harness-owned history."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.events import TurnFailed
from corki.protocol.items import UserMessageItem
from corki.tools import ToolRegistry

META = "internal_chat_message_metadata_passthrough"
OPAQUE = {"type": "compaction", "encrypted_content": "checkpoint"}
BAD = [
    {"type": "local_shell_call", "status": "completed", "action": {"type": "exec"}},
    {"type": "configuration_update", "reasoning": {"effort": ""}},
    {"type": "web_search_call", "action": {"type": "search", "queries": [1]}},
    {"type": "reasoning", "summary": [], "content": [{"type": "text", "text": 1}]},
    {"type": "tool_search_call", "execution": "client"},
    {
        "type": "function_call_output",
        "output": [{"type": "input_image", "image_url": "x", "detail": "bad"}],
    },
    {
        "type": "function_call",
        "name": "x",
        "call_id": "c",
        "arguments": "{}",
        "encrypted_function_args": [1],
    },
    {"type": "message", "role": "developer", "content": [], META: {"turn_id": 3}},
    {**OPAQUE, META: {"create_time": True}},
]


def config(tmp_path, v2=False, token_budget=False):
    return CorkiSettings(
        tmp_path,
        api_mode="responses",
        provider_name="openai",
        api_key="fixture",
        api_base="https://fixture.invalid/v1",
        skills_enabled=False,
        remote_compaction_v2=v2,
        token_budget_enabled=token_budget,
        model_request_max_retries=0,
        model_max_retries=0,
    )


def packet(items):
    events = [{"type": "response.output_item.done", "item": item} for item in items]
    events.append({"type": "response.completed", "response": {"id": "response"}})
    return httpx.Response(200, text="".join(f"data: {json.dumps(e)}\n\n" for e in events))


def install_http(monkeypatch, respond):
    real = httpx.AsyncClient
    monkeypatch.setattr(
        http_client,
        "OwnedHTTPClient",
        lambda *a, **kw: real(*a, **kw, transport=httpx.MockTransport(respond)),
    )


@pytest.mark.parametrize("v2", [False, True])
@pytest.mark.parametrize("bad", BAD)
@pytest.mark.parametrize("token_budget", [False, True])
@pytest.mark.parametrize("provider", ["openai", "independent"])
@pytest.mark.parametrize("base_url", ["https://api.openai.com/v1", "https://fixture.invalid/v1"])
def test_non_summary_response_preserves_history(
    tmp_path, monkeypatch, bad, v2, token_budget, provider, base_url
):
    async def scenario():
        requests = []

        def respond(request):
            requests.append(request)
            assert str(request.url) == base_url + "/responses"
            assert request.headers["authorization"] == "Bearer fixture"
            assert not any(
                key.startswith(("x-codex-", "x-openai-"))
                or key in {"chatgpt-account-id", "openai-beta"}
                for key in request.headers
            )
            body = json.loads(request.content)
            assert not body.get("tools")
            assert not any(i.get("type") == "compaction_trigger" for i in body["input"])
            return packet([OPAQUE, bad])

        install_http(monkeypatch, respond)
        runtime = LangGraphRuntime.create(
            settings=replace(
                config(tmp_path, v2, token_budget), provider_name=provider, api_base=base_url
            ),
            database_path=tmp_path / "s.db",
            registry=ToolRegistry(),
        )
        try:
            await runtime._ensure_ready()
            old = UserMessageItem("keep original", "old")
            await runtime._repository.append_items(runtime.thread_id, (old,))
            events = [e async for e in runtime.compact()]
            assert isinstance(events[-1], TurnFailed)
            assert await runtime._repository.load_items(runtime.thread_id) == (old,)
            assert len(requests) == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
