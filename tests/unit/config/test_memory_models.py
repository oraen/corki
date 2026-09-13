import pytest

from corki.config import CorkiSettings
from corki.memory.inputs import extraction_token_budget


@pytest.mark.parametrize(
    "provider,stage,expected",
    [
        ("", "", ("main", "main")),
        (
            'memory_extraction_model="small"\nmemory_consolidation_model="large"',
            "",
            ("small", "large"),
        ),
        ("", 'extraction_model="chosen"\nconsolidation_model="chosen2"', ("chosen", "chosen2")),
        (
            'memory_extraction_model="small"\nmemory_consolidation_model="large"',
            'extraction_model="chosen"',
            ("chosen", "large"),
        ),
    ],
)
def test_stage_override_precedes_provider_preferences_then_main_model(
    tmp_path, provider, stage, expected
):
    config = tmp_path / "config.toml"
    config.write_text(f'[agent]\nmodel="main"\n[provider]\n{provider}\n[memories]\n{stage}\n')
    settings = CorkiSettings.for_directory(tmp_path, config_file=config)
    assert (
        settings.resolved_memory_extraction_model,
        settings.resolved_memory_consolidation_model,
    ) == expected


@pytest.mark.parametrize(
    "catalog,expected",
    [(None, 872000), ("", 272000), ("[models.catalog.other]\ncontext_window=5000", 272000)],
)
def test_bundled_memory_window_and_authoritative_custom_catalog(tmp_path, catalog, expected):
    config = tmp_path / "config.toml"
    config.write_text(
        '[memories]\nextraction_model="gpt-5.6-luna"\n[models]\ncontext_window_override=1000000\n'
        + ("[models.catalog]\n" if catalog == "" else catalog or "")
    )
    settings = CorkiSettings.for_directory(tmp_path, config_file=config)
    info = settings.model_context_info("gpt-5.6-luna")
    assert info.context_window == expected
    assert extraction_token_budget(settings) == (expected * 95 // 100) * 70 // 100


@pytest.mark.parametrize("value", ["true", "0", '""', "[]"])
def test_provider_memory_preferences_reject_invalid_ids(tmp_path, value):
    config = tmp_path / "config.toml"
    config.write_text(f"[provider]\nmemory_extraction_model={value}\n")
    with pytest.raises(ValueError, match="memory_extraction_model"):
        CorkiSettings.for_directory(tmp_path, config_file=config)
