import pytest

from corki.config import CorkiSettings
from corki.protocol.context import ContextLimits


def test_model_info_distinct_window_limits_match_codex_source_example():
    limits = ContextLimits(272000, 250000)
    assert (limits.raw_tokens, limits.usable_tokens, limits.auto_compact_limit) == (
        272000,
        258400,
        244800,
    )
    assert ContextLimits(400000).usable_tokens == 380000
    assert ContextLimits(400000).auto_compact_limit == 360000
    assert ContextLimits(272000, 100000).auto_compact_limit == 100000
    assert ContextLimits(272000, 400000).auto_compact_limit == 244800
    assert ContextLimits(272000, effective_percent=80).trigger_tokens == 217600


@pytest.mark.parametrize("percent", [0, 101, -1, True, 95.5, "95"])
def test_headroom_configuration_rejects_invalid_values(tmp_path, percent):
    with pytest.raises(ValueError):
        CorkiSettings(working_directory=tmp_path, effective_context_window_percent=percent)


def test_window_defaults_follow_custom_raw_size_and_configured_limits(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("[agent]\ncontext_window_tokens=8000\neffective_context_window_percent=90\n")
    settings = CorkiSettings.for_directory(tmp_path, config_file=config)
    assert settings.auto_compact_tokens is None
    limits = ContextLimits(
        settings.context_window_tokens,
        settings.auto_compact_tokens,
        settings.effective_context_window_percent,
    )
    assert limits.usable_tokens == 7200
    assert limits.auto_compact_limit == 7200


@pytest.mark.parametrize("limit", [0, -1, True, "700", 700.5])
def test_invalid_explicit_auto_compact_limit_is_rejected(tmp_path, limit):
    with pytest.raises(ValueError):
        CorkiSettings(working_directory=tmp_path, auto_compact_tokens=limit)
