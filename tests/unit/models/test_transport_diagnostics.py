"""Blank HTTPX failures retain an actionable error type on both model protocols."""

import asyncio

import httpx
import pytest

from corki.models import (
    ModelError,
    ModelRequest,
    OpenAICompatibleModel,
    OpenAIResponsesModel,
    resolve_capabilities,
)
from corki.protocol.ids import new_turn_id
from corki.protocol.items import UserMessageItem


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("error_type", [httpx.ReadError, httpx.ReadTimeout])
def test_empty_transport_error_includes_its_type(mode, error_type):
    async def scenario():
        def fail(_request):
            raise error_type("")

        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            model_type = (
                OpenAICompatibleModel if mode == "chat_completions" else OpenAIResponsesModel
            )
            model = model_type(
                api_key="fixture-private-key",
                base_url="https://fixture.invalid",
                capabilities=resolve_capabilities(
                    base_url="https://fixture.invalid", api_mode=mode
                ),
                client=client,
                max_retries=0,
                request_max_retries=0,
            )
            request = ModelRequest(
                "fixture", "system", (), (UserMessageItem("hello", new_turn_id()),), ()
            )
            with pytest.raises(ModelError, match=error_type.__name__) as caught:
                async for _event in model.stream(request):
                    pass
            assert "fixture-private-key" not in str(caught.value)

    asyncio.run(scenario())
