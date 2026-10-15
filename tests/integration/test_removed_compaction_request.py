"""Obsolete SDK compaction controls fail closed before any network request."""

import asyncio

import httpx
import pytest

from corki.models.base import ModelError, ModelErrorKind
from corki.models.capabilities import ProviderCapabilities
from corki.models.responses import OpenAIResponsesModel
from corki.models.types import ModelRequest
from corki.protocol.items import UserMessageItem


@pytest.mark.parametrize("provider", ["openai", "independent"])
@pytest.mark.parametrize("mode", ["v2", "legacy"])
@pytest.mark.parametrize("empty", [False, True])
def test_old_compaction_controls_cannot_reactivate_transport(provider, mode, empty):
    async def scenario():
        calls = []

        def respond(request):
            calls.append(request)
            return httpx.Response(200, json={"output": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = OpenAIResponsesModel(
                api_key="fixture",
                base_url="https://fixture.invalid/v1",
                capabilities=ProviderCapabilities(name=provider, api_mode="responses"),
                client=client,
                max_retries=0,
            )
            request = ModelRequest(
                model="fixture",
                instructions="",
                context_items=(),
                items=() if empty else (UserMessageItem("old history", "turn"),),
                tools=(),
                compaction_turn_id="turn",
                compaction_mode=mode,
            )
            with pytest.raises(ModelError, match="dedicated compaction") as error:
                _ = [event async for event in model.stream(request)]
            assert error.value.kind is ModelErrorKind.PROTOCOL
            assert not calls

    asyncio.run(scenario())
