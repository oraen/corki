"""Provider labels and lookalike hosts do not choose private model protocols."""

import asyncio
import json

import httpx
import pytest

from corki import http_client
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.protocol.events import ContextCompacted, TurnCompleted, TurnFailed
from corki.protocol.tools import ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("provider", ["openai", "deepseek", "custom", "DEEPSEEK"])
@pytest.mark.parametrize(
    "host", ["fixture.invalid", "deepseek.com.fixture.invalid", "api.openai.com"]
)
@pytest.mark.parametrize("model", ["fixture", "gpt-5"])
@pytest.mark.parametrize("phase", ["manual", "pre_turn", "mid_turn"])
def test_labels_cannot_change_turn_summary_or_cold_transport(
    tmp_path, monkeypatch, mode, provider, host, model, phase
):
    async def scenario():
        requests = []
        summaries, effects = [], []
        normal_count = 0
        base = f"https://{host}/v1"
        compact_prompt = "SUMMARIZE_UNIFORM_TRANSPORT"

        class Effect:
            spec = ToolSpec("effect", "One observable effect", {"type": "object"})

            async def execute(self, call, context):
                effects.append(call.id)
                return ToolResult(call.id, call.name, "ONE_OBSERVATION")

        def respond(request):
            nonlocal normal_count
            assert str(request.url) == base + (
                "/responses" if mode == "responses" else "/chat/completions"
            )
            body = json.loads(request.content)
            requests.append(body)
            assert body["model"] == model
            assert request.headers["authorization"] == "Bearer fixture"
            assert "thinking" not in body
            assert not any(
                name.startswith(("x-openai-", "x-codex-"))
                or name in {"chatgpt-account-id", "openai-beta"}
                for name in request.headers
            )
            assert (
                not {"compaction_trigger", "context_management", "previous_response_id"}
                & body.keys()
            )
            messages = body.get("input", body.get("messages"))
            is_summary = compact_prompt in json.dumps(messages[-1])
            if is_summary:
                summaries.append(body)
                assert not body.get("tools")
            else:
                normal_count += 1
            call = not is_summary and phase == "mid_turn" and normal_count == 1
            high = not is_summary and phase != "manual" and normal_count == 1
            usage = 120_000 if high else 100
            text = "SUMMARY" if is_summary else "done"
            item = (
                {
                    "type": "function_call",
                    "name": "effect",
                    "call_id": "effect-once",
                    "arguments": "{}",
                }
                if call
                else {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                }
            )
            delta = (
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "effect-once",
                            "type": "function",
                            "function": {"name": "effect", "arguments": "{}"},
                        }
                    ]
                }
                if call
                else {"content": text}
            )
            event = (
                {
                    "type": "response.completed",
                    "response": {
                        "id": "r",
                        "output": [item],
                        "usage": {
                            "input_tokens": usage,
                            "output_tokens": 10,
                            "total_tokens": usage + 10,
                        },
                    },
                }
                if mode == "responses"
                else {
                    "choices": [
                        {"delta": delta, "finish_reason": "tool_calls" if call else "stop"}
                    ],
                    "usage": {"prompt_tokens": usage, "completion_tokens": 10},
                }
            )
            return httpx.Response(
                200,
                text="data: "
                + json.dumps(event)
                + "\n\n"
                + ("data: [DONE]\n\n" if mode != "responses" else ""),
            )

        client = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *a, **kw: client(*a, **kw, transport=httpx.MockTransport(respond)),
        )

        async def create(thread=None):
            registry = ToolRegistry()
            registry.register(Effect())
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    tmp_path,
                    model=model,
                    provider_name=provider,
                    api_mode=mode,
                    api_base=base,
                    api_key="fixture",
                    thinking_enabled=True,
                    skills_enabled=False,
                    plugins_enabled=False,
                    context_window_tokens=200_000,
                    auto_compact_tokens=100_000,
                    compact_prompt=compact_prompt,
                    model_request_max_retries=0,
                    model_max_retries=0,
                ),
                database_path=tmp_path / "session.db",
                home_path=tmp_path / "home",
                registry=registry,
                thread_id=thread,
            )

        runtime = await create()
        try:
            events = [e async for e in runtime.stream("first")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert sum(isinstance(e, ContextCompacted) for e in events) == (phase == "mid_turn")
            if phase != "mid_turn":
                before_compact = await runtime._repository.load_items(runtime.thread_id)
                stream = runtime.compact() if phase == "manual" else runtime.stream("second")
                events = [e async for e in stream]
                assert isinstance(events[-1], TurnCompleted), events[-1]
                assert sum(isinstance(e, ContextCompacted) for e in events) == 1
                stored = await runtime._repository.load_items(runtime.thread_id)
                assert stored[: len(before_compact)] == before_compact
            assert len(summaries) == 1
            assert effects == (["effect-once"] if phase == "mid_turn" else [])
            if phase == "mid_turn":
                assert "ONE_OBSERVATION" in json.dumps(summaries[0])
            thread = runtime.thread_id
            before = await runtime._repository.load_items(thread)
        finally:
            await runtime.aclose()
        cold = await create(thread)
        try:
            assert isinstance([e async for e in cold.stream("next")][-1], TurnCompleted)
            assert len(requests) == (3 if phase == "manual" else 4)
            assert len(summaries) == 1 and "SUMMARY" in json.dumps(requests[-1])
            assert effects == (["effect-once"] if phase == "mid_turn" else [])
            stored = await cold._repository.load_items(thread)
            assert stored[: len(before)] == before
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("provider", ["openai", "independent"])
@pytest.mark.parametrize("missing", ["base", "key"])
def test_missing_configuration_never_falls_back_on_warm_or_cold_turn(
    tmp_path, monkeypatch, mode, provider, missing
):
    async def scenario():
        requests = []

        def forbidden(request):
            requests.append(request)
            raise AssertionError("missing configuration must fail before any HTTP request")

        client_type = httpx.AsyncClient
        monkeypatch.setattr(
            http_client,
            "OwnedHTTPClient",
            lambda *args, **kwargs: client_type(
                *args, **kwargs, transport=httpx.MockTransport(forbidden)
            ),
        )
        settings = CorkiSettings(
            tmp_path,
            model="gpt-5",
            provider_name=provider,
            api_mode=mode,
            api_base="" if missing == "base" else "https://fixture.invalid/v1",
            api_key="fixture-key" if missing == "base" else "",
            skills_enabled=False,
            plugins_enabled=False,
            model_max_retries=0,
            model_request_max_retries=0,
        )
        thread = None
        for prompt in ("initial", "after cold restart"):
            runtime = await LangGraphRuntime.acreate(
                settings=settings,
                database_path=tmp_path / "state.db",
                home_path=tmp_path / "home",
                thread_id=thread,
                registry=ToolRegistry(),
            )
            try:
                events = [event async for event in runtime.stream(prompt)]
                failures = [event for event in events if isinstance(event, TurnFailed)]
                assert len(failures) == 1 and events[-1] is failures[0]
                assert (
                    "No model base URL configured" if missing == "base" else "No API key configured"
                ) in failures[0].error
                assert not any(isinstance(event, TurnCompleted) for event in events)
                assert requests == []
                thread = runtime.thread_id
            finally:
                await runtime.aclose()

    asyncio.run(scenario())
