import asyncio
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings
from corki.models import ModelRequest, OpenAIResponsesModel, resolve_capabilities
from corki.models.request_settings import resolve_reasoning
from corki.protocol.context import ModelContextInfo


@pytest.mark.parametrize("value", ["invalid", "", False, [], 1])
def test_summary_selection_is_validated_at_settings_and_request_boundaries(tmp_path, value):
    with pytest.raises(ValueError, match="reasoning_summary"):
        CorkiSettings(tmp_path, reasoning_summary=value)
    with pytest.raises(ValueError, match="reasoning_summary"):
        ModelRequest("m", "", (), (), (), reasoning_summary=value)
    with pytest.raises(ValueError, match="reasoning_summary"):
        ModelContextInfo("m", default_reasoning_summary=value)


@pytest.mark.parametrize("value", [None, 1, "true", []])
def test_model_summary_parameter_gate_is_strictly_boolean(value):
    with pytest.raises(ValueError, match="summary_parameter"):
        ModelContextInfo("m", supports_reasoning_summary_parameter=value)


def test_model_snapshot_identity_and_explicit_resolved_none():
    info = ModelContextInfo(
        "m", default_reasoning_level="high", default_reasoning_summary="detailed"
    )
    request = ModelRequest("m", "", (), (), (), model_info=info)
    assert resolve_reasoning(request, None) == {"effort": "high", "summary": "detailed"}
    assert resolve_reasoning(request, "ultra") == {"effort": "high", "summary": "detailed"}
    assert resolve_reasoning(replace(request, reasoning_effort_resolved=True), "ultra") == {
        "summary": "detailed"
    }
    assert resolve_reasoning(replace(request, reasoning_effort="low"), "ultra") == {
        "effort": "low",
        "summary": "detailed",
    }
    with pytest.raises(ValueError, match="model_info"):
        replace(request, model="other")


@pytest.mark.parametrize("supported", [True, False])
def test_model_summary_resolution_preserves_provider_support_and_shared_defaults(supported):
    async def scenario():
        async with httpx.AsyncClient() as client:
            model = OpenAIResponsesModel(
                api_key="fixture",
                base_url="https://fixture.invalid",
                client=client,
                reasoning_effort="high",
                capabilities=replace(
                    resolve_capabilities(base_url="https://fixture.invalid", api_mode="responses"),
                    supports_reasoning_effort=supported,
                ),
            )
            info = ModelContextInfo("m", default_reasoning_summary="none")
            resolved = ModelRequest(
                "m", "", (), (), (), model_info=info, reasoning_effort_resolved=True
            )
            body = model._build_payload(resolved)
            assert body.get("reasoning") == ({} if supported else None)
            assert body.get("include") == (["reasoning.encrypted_content"] if supported else None)
            following = model._build_payload(ModelRequest("m", "", (), (), ()))
            assert following.get("reasoning") == (
                {"effort": "high", "summary": "auto"} if supported else None
            )

    asyncio.run(scenario())
