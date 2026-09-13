"""Ordinary usage survives private billing fields and headers across turns."""

import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import OpenAIResponsesModel, resolve_capabilities
from corki.protocol.events import TurnCompleted
from corki.tools import ToolRegistry


@pytest.mark.parametrize("provider", ["openai", "independent"])
@pytest.mark.parametrize("private", [3.5, {"not": "a number"}])
@pytest.mark.parametrize("header", [False, True])
def test_runtime_uses_only_ordinary_token_counts(tmp_path, provider, private, header):
    async def scenario():
        requests = []

        def respond(request):
            requests.append(request)
            assert str(request.url) == "https://fixture.invalid/v1/responses"
            event = {
                "type": "response.completed",
                "response": {
                    "id": f"r-{len(requests)}",
                    "usage_metadata": {"amount": ["private"]},
                    "usage": {
                        "input_tokens": 50,
                        "output_tokens": 10,
                        "total_tokens": 60,
                        "input_tokens_details": {"cached_tokens": 4},
                        "output_tokens_details": {"reasoning_tokens": 2},
                        "codex_rollout_budget_units": private,
                    },
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "done"}],
                        }
                    ],
                },
            }
            return httpx.Response(
                200,
                text="data: " + json.dumps(event) + "\n\n",
                headers={"x-reasoning-included": "true"} if header and len(requests) == 1 else {},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        model = OpenAIResponsesModel(
            api_key="fixture",
            base_url="https://fixture.invalid/v1",
            client=client,
            capabilities=resolve_capabilities(
                base_url="https://fixture.invalid/v1", api_mode="responses", provider_name=provider
            ),
        )
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                tmp_path, skills_enabled=False, api_mode="responses", model_max_retries=0
            ),
            model=model,
            registry=ToolRegistry(),
            database_path=tmp_path / "history.db",
        )
        try:
            for text in ("first", "second"):
                events = [event async for event in runtime.stream(text)]
                assert isinstance(events[-1], TurnCompleted), events[-1]
                usage = await runtime._repository.load_context_usage(runtime.thread_id)
                assert usage.input_tokens == 50 and usage.total_tokens == 60
                assert not usage.server_reasoning_included
                result = await runtime._repository.load_model_step(
                    runtime.thread_id, events[-1].turn_id, 0
                )
                assert result.usage.cached_tokens == 4 and result.usage.reasoning_tokens == 2
                assert result.usage.codex_rollout_budget_units is None
                assert "usage_metadata" not in result.provider_metadata
                assert "server_reasoning_included" not in result.provider_metadata
            assert len(requests) == 2
        finally:
            await runtime.aclose()
            await client.aclose()

    asyncio.run(scenario())
