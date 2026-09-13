import asyncio
from dataclasses import replace

import httpx
import pytest

from corki.models import (
    ModelRequest,
    OpenAICompatibleModel,
    OpenAIResponsesModel,
    resolve_capabilities,
)


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("supported", [True, False])
def test_request_effort_overrides_only_its_request_and_obeys_capability(mode, supported):
    async def scenario():
        client = httpx.AsyncClient()
        capabilities = replace(
            resolve_capabilities(base_url="https://example.test", api_mode=mode),
            supports_reasoning_effort=supported,
        )
        adapter = OpenAICompatibleModel if mode == "chat_completions" else OpenAIResponsesModel
        model = adapter(
            api_key="test",
            base_url="https://example.test",
            capabilities=capabilities,
            reasoning_effort="high",
            client=client,
        )
        try:
            request = ModelRequest("model", "", (), (), (), reasoning_effort="low")
            first = model._build_payload(request)
            following = model._build_payload(replace(request, reasoning_effort=None))
            cleared = model._build_payload(
                replace(request, reasoning_effort=None, reasoning_effort_resolved=True)
            )
            if mode == "chat_completions":
                assert first.get("reasoning_effort") == ("low" if supported else None)
                assert following.get("reasoning_effort") == ("high" if supported else None)
                assert "reasoning_effort" not in cleared
            else:
                assert first.get("reasoning", {}).get("effort") == ("low" if supported else None)
                assert following.get("reasoning", {}).get("effort") == (
                    "high" if supported else None
                )
                assert "reasoning" not in cleared
        finally:
            await model.aclose()
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("value", ["", 1, False, []])
def test_request_effort_requires_a_non_empty_string(value):
    with pytest.raises(ValueError, match="reasoning_effort"):
        ModelRequest("model", "", (), (), (), reasoning_effort=value)
