"""Static configuration and request-level service-tier contracts."""

import pytest

from corki.config import CorkiSettings
from corki.config.model_context import parse_model_contexts
from corki.models import ModelRequest
from corki.models.request_settings import resolve_service_tier
from corki.protocol.context import ModelContextInfo


@pytest.mark.parametrize("value", [1, False, [], {}])
def test_service_tier_selection_rejects_non_string(tmp_path, value):
    with pytest.raises(ValueError, match="service_tier"):
        CorkiSettings(tmp_path, service_tier=value)
    with pytest.raises(ValueError, match="service_tier"):
        ModelRequest("m", "", (), (), (), service_tier=value)


@pytest.mark.parametrize("value", [None, 1, "true", []])
def test_fast_mode_requires_boolean(tmp_path, value):
    with pytest.raises(ValueError, match="fast_mode"):
        CorkiSettings(tmp_path, fast_mode=value)
    with pytest.raises(ValueError, match="fast_mode"):
        ModelRequest("m", "", (), (), (), fast_mode_enabled=value)


@pytest.mark.parametrize("value", [None, False, {}, ["priority"], [{}], [{"id": 1}]])
def test_catalog_requires_service_tier_id_tables(value):
    with pytest.raises(ValueError, match="service_tiers"):
        parse_model_contexts({"m": {"service_tiers": value}})


@pytest.mark.parametrize(
    "selected,expected",
    [(None, None), ("default", None), ("priority", "priority"), ("unknown", None), ("", "")],
)
def test_request_filter_never_uses_catalog_default(selected, expected):
    info = ModelContextInfo("m", service_tiers=("priority", ""), default_service_tier="priority")
    request = ModelRequest("m", "", (), (), (), model_info=info, service_tier=selected)
    assert resolve_service_tier(request) == expected


def test_pinned_catalog_tiers_and_config_normalization(tmp_path):
    settings = CorkiSettings(tmp_path, model="gpt-5.6-sol", service_tier="fast")
    assert settings.service_tier == settings.session_service_tier == "priority"
    assert settings.model_context_info("gpt-5.6-sol-v2").service_tiers == ("priority", "ultrafast")
    assert settings.model_context_info("gpt-5.2").service_tiers == ()
    assert (
        CorkiSettings(
            tmp_path, model="gpt-5.6-sol", fast_mode=False, service_tier="fast"
        ).service_tier
        is None
    )
    assert (
        CorkiSettings(
            tmp_path, model="gpt-5.6-sol", fast_mode=False, service_tier="flex"
        ).service_tier
        == "flex"
    )
