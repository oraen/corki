"""Ordinary Responses ignores legacy SDK metadata, including official provider labels."""

import asyncio
import json

import httpx
import pytest

from corki.models import ModelCompleted, ModelRequest, OpenAIResponsesModel, resolve_capabilities
from corki.protocol.items import UserMessageItem


@pytest.mark.parametrize("provider", ["openai", "independent"])
@pytest.mark.parametrize("base", ["https://api.openai.com/v1", "https://fixture.invalid/v1"])
def test_sdk_metadata_is_not_sent_on_initial_request_or_retry(provider, base):
    async def scenario():
        seen = []
        metadata = {
            "x-codex-window-id": "private-window",
            "x-codex-turn-metadata": '{"tool_namespaces_info":{"private-tool":{}}}',
            "session_id": "private-session",
        }

        def respond(request):
            seen.append(request)
            assert str(request.url) == base + "/responses"
            body = json.loads(request.content)
            assert "client_metadata" not in body
            assert "x-codex-window-id" not in request.headers
            assert "x-codex-turn-metadata" not in request.headers
            assert "private-" not in json.dumps(body)
            if len(seen) == 1:
                return httpx.Response(503)
            event = {"type": "response.completed", "response": {"id": "r", "output": []}}
            return httpx.Response(200, text="data: " + json.dumps(event) + "\n\n")

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = OpenAIResponsesModel(
                api_key="fixture",
                base_url=base,
                client=client,
                capabilities=resolve_capabilities(
                    base_url=base,
                    api_mode="responses",
                    provider_name=provider,
                ),
            )
            request = ModelRequest(
                "fixture",
                "instructions",
                (),
                (UserMessageItem("hello", "turn"),),
                (),
                client_metadata=metadata,
            )
            try:
                events = [event async for event in model.stream(request)]
                assert any(isinstance(event, ModelCompleted) for event in events)
                assert len(seen) == 2
                assert metadata["session_id"] == "private-session"
            finally:
                await model.aclose()

    asyncio.run(scenario())
