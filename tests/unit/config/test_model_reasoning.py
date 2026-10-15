import pytest

from corki.config import CorkiSettings
from corki.config.model_context import parse_model_contexts
from corki.protocol.context import ModelContextInfo


@pytest.mark.parametrize(
    "invalid", [None, False, {}, ["low"], [{}], [{"effort": ""}], [{"effort": 1}]]
)
def test_catalog_rejects_invalid_reasoning_levels(invalid):
    with pytest.raises(ValueError, match="supported_reasoning_levels"):
        parse_model_contexts({"m": {"supported_reasoning_levels": invalid}})


@pytest.mark.parametrize("field", ["default_reasoning_level", "multi_agent_reasoning_effort"])
@pytest.mark.parametrize("invalid", [False, "", [], 1])
def test_catalog_rejects_invalid_reasoning_metadata(field, invalid):
    with pytest.raises(ValueError, match=field):
        parse_model_contexts({"m": {field: invalid}})


@pytest.mark.parametrize(
    "preferred,expected", [("low", "low"), ("ultra", "max"), ("missing", "max"), (None, "max")]
)
def test_ultra_request_resolution_respects_valid_preference(preferred, expected):
    info = ModelContextInfo(
        "m",
        supported_reasoning_levels=("low", "max", "ultra"),
        multi_agent_reasoning_effort=preferred,
    )
    assert info.reasoning_effort_for_model_switch("ultra") == "ultra"
    assert info.reasoning_effort_for_request("ultra") == expected


def test_catalog_lookup_and_window_override_preserve_reasoning(tmp_path):
    settings = CorkiSettings(tmp_path, model="gpt-6-astra", context_window_tokens=10000)
    info = settings.model_context_info("scope/gpt-6-astra-version")
    assert info.model == "scope/gpt-6-astra-version"
    assert info.with_override(12345).reasoning_effort_for_request("ultra") == "xhigh"
    assert info.reasoning_effort_for_model_switch(None) == "high"
    assert info.default_reasoning_level == "low"
    assert (
        settings.model_context_info("gpt-5.6-luna").reasoning_effort_for_request("ultra") == "max"
    )
    assert (
        settings.model_context_info("gpt-5.5").reasoning_effort_for_model_switch(None) == "medium"
    )
