"""Automatic local compaction restores guidance through both ordinary transports."""

import asyncio
import json

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.plugins.context import PluginContextContributor
from corki.plugins.models import LoadedPlugin, PluginManifest
from corki.protocol.events import ContextCompacted, TurnCompleted
from corki.protocol.items import CompactionItem, ContextItem, ToolResultItem
from corki.protocol.tools import ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("phase", ["pre_turn", "mid_turn"])
def test_automatic_compaction_guidance_wire_and_cold_history(
    tmp_path, monkeypatch, api_mode, phase
):
    async def scenario():
        normal, summaries, executed = [], [], []

        class Source:
            def contributions(self, **kwargs):
                plugin = LoadedPlugin(PluginManifest("fixture", None, "LOCAL_PLUGIN", tmp_path))
                return PluginContextContributor((plugin,)).contributions(**kwargs)

        class Advance:
            spec = ToolSpec("advance", "Continue work", {"type": "object"})

            async def execute(self, call, context):
                executed.append(call.id)
                return ToolResult(call.id, call.name, "OBSERVED_ONCE")

        def respond(request):
            assert request.url.host == "fixture.invalid"
            assert request.url.path == (
                "/v1/responses" if api_mode == "responses" else "/v1/chat/completions"
            )
            body = json.loads(request.content)
            summary = not body.get("tools")
            (summaries if summary else normal).append(body)
            index = len(normal)
            call = not summary and index == 1 and phase == "mid_turn"
            tokens = 60_000 if not summary and index == 1 else 100
            answer = "Saved progress" if summary else "done"
            messages = body["input" if api_mode == "responses" else "messages"]
            if api_mode == "responses":
                calls = {m["call_id"] for m in messages if m.get("type") == "function_call"}
                results = {
                    m["call_id"] for m in messages if m.get("type") == "function_call_output"
                }
            else:
                calls = {c["id"] for m in messages for c in m.get("tool_calls", [])}
                results = {m["tool_call_id"] for m in messages if m.get("role") == "tool"}
            assert calls == results
            if not summary:
                guidance = [
                    m for m in messages if "Plugins are not invoked directly" in json.dumps(m)
                ]
                assert len(guidance) == 1
                assert guidance[0]["role"] == ("developer" if api_mode == "responses" else "system")
            if summary and phase == "mid_turn":
                assert "OBSERVED_ONCE" in json.dumps(messages)
            if api_mode == "responses":
                output = (
                    {
                        "type": "function_call",
                        "id": "item",
                        "call_id": "once",
                        "name": "advance",
                        "arguments": "{}",
                    }
                    if call
                    else {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": answer}],
                    }
                )
                packet = {
                    "type": "response.completed",
                    "response": {
                        "id": f"r-{index}-{summary}",
                        "output": [output],
                        "usage": {
                            "input_tokens": tokens,
                            "output_tokens": 10,
                            "total_tokens": tokens + 10,
                        },
                    },
                }
                wire = f"data: {json.dumps(packet)}\n\n"
            else:
                delta = (
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "once",
                                "type": "function",
                                "function": {"name": "advance", "arguments": "{}"},
                            }
                        ]
                    }
                    if call
                    else {"content": answer}
                )
                packet = {
                    "choices": [
                        {"delta": delta, "finish_reason": "tool_calls" if call else "stop"}
                    ],
                    "usage": {"prompt_tokens": tokens, "completion_tokens": 10},
                }
                wire = f"data: {json.dumps(packet)}\n\ndata: [DONE]\n\n"
            return httpx.Response(200, text=wire)

        client_type = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: client_type(*a, **kw, transport=httpx.MockTransport(respond)),
        )

        async def create(thread=None):
            registry = ToolRegistry()
            registry.register(Advance())
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    tmp_path,
                    skills_enabled=False,
                    plugins_enabled=False,
                    api_mode=api_mode,
                    api_key="fixture",
                    api_base="https://fixture.invalid/v1",
                    context_window_tokens=100_000,
                    auto_compact_tokens=50_000,
                ),
                registry=registry,
                context_contributors=(Source(),),
                thread_id=thread,
                database_path=tmp_path / "state.db",
                home_path=tmp_path / "home",
            )

        runtime = await create()
        try:
            events = [e async for e in runtime.stream("original")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            if phase == "pre_turn":
                assert not summaries
                events += [e async for e in runtime.stream("next")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(normal) == 2 and len(summaries) == 1
            assert sum(isinstance(e, ContextCompacted) for e in events) == 1
            assert executed == (["once"] if phase == "mid_turn" else [])
            original = await runtime._repository.load_items(runtime.thread_id)
            assert sum(isinstance(i, CompactionItem) for i in original) == 1
            assert sum(isinstance(i, ToolResultItem) for i in original) == len(executed)
            assert (
                sum(
                    isinstance(i, ContextItem) and i.content_kind == "plugins.usage_instructions"
                    for i in original
                )
                == 2
            )
        finally:
            await runtime.aclose()
        cold = await create(runtime.thread_id)
        try:
            assert [e async for e in cold.resume_pending()] == []
            assert len(normal) == 2
            assert isinstance([e async for e in cold.stream("cold")][-1], TurnCompleted)
            assert len(normal) == 3 and len(summaries) == 1
            assert executed == (["once"] if phase == "mid_turn" else [])
            after = await cold._repository.load_items(cold.thread_id)
            assert after[: len(original)] == original
        finally:
            await cold.aclose()

    asyncio.run(scenario())
