import pytest

from corki.config import CorkiSettings


@pytest.mark.parametrize(
    "catalog,override,cap,expected",
    [
        ("context_window=123000\neffective_context_window_percent=95", None, None, 81795),
        ("max_context_window=10000\neffective_context_window_percent=80", None, None, 5600),
        ("context_window=9000\nmax_context_window=10000", 20000, None, 6650),
        ("context_window=1000000", None, None, 665000),
        ("context_window=10000", None, 300, 300),
        ("", None, None, 150000),
        ("", 1000, None, 665),
        ("", None, 2000, 2000),
        ("context_window=1", None, None, 1),
    ],
)
def test_extraction_budget_resolves_selected_model_metadata(
    tmp_path, catalog, override, cap, expected
):
    config = tmp_path / "config.toml"
    config.write_text(
        '[agent]\nmodel="main"\ncontext_window_tokens=33333\n'
        '[memories]\nextraction_model="extract"\n'
        + (f"extraction_token_limit={cap}\n" if cap is not None else "")
        + "[models]\n"
        + (f"context_window_override={override}\n" if override is not None else "")
        + "[models.catalog.extract]\n"
        + catalog
        + "\n"
    )
    settings = CorkiSettings.for_directory(tmp_path, config_file=config)
    from corki.memory.inputs import extraction_token_budget

    assert extraction_token_budget(settings) == expected


def test_explicit_main_extraction_model_uses_main_configured_effective_window(tmp_path):
    from corki.memory.inputs import extraction_token_budget

    settings = CorkiSettings(
        working_directory=tmp_path,
        context_window_tokens=12345,
        effective_context_window_percent=80,
        memories_extraction_model="gpt-5",
    )
    assert extraction_token_budget(settings) == (12345 * 80 // 100) * 70 // 100


@pytest.mark.parametrize("override", [None, 1000000])
def test_missing_model_uses_codex_generic_metadata_not_missing_window_fallback(tmp_path, override):
    from corki.memory.inputs import extraction_token_budget

    settings = CorkiSettings(
        working_directory=tmp_path,
        memories_extraction_model="unknown",
        model_context_window_override=override,
    )
    assert extraction_token_budget(settings) == 180880


@pytest.mark.parametrize(
    "body",
    [
        "[models]\ncatalog=[]",
        "[models]\ncontext_window_override=true",
        "[models.catalog.extract]\ncontext_window=0",
        "[models.catalog.extract]\nmax_context_window=-1",
        "[models.catalog.extract]\neffective_context_window_percent=101",
        '[models.catalog.extract]\ncontext_window="123"',
        '[models.catalog."gpt-5"]\ncontext_window=1',
    ],
)
def test_invalid_model_window_metadata_is_not_silently_ignored(tmp_path, body):
    config = tmp_path / "config.toml"
    config.write_text(body)
    with pytest.raises(ValueError):
        CorkiSettings.for_directory(tmp_path, config_file=config)


@pytest.mark.parametrize(
    "value,budget,expected",
    [
        ("abcdefghij", 1, "ab…2 tokens truncated…ij"),
        ("你好世界", 1, "…2 tokens truncated…"),
        ("你好世界", 2, "你…1 tokens truncated…界"),
        ("a😀b😀c", 2, "a…1 tokens truncated…c"),
        ("keep all", 2, "keep all"),
        ("", 1, ""),
    ],
)
def test_rollout_truncation_matches_utf8_head_tail_policy(value, budget, expected):
    from corki.memory.inputs import truncate_rollout

    assert truncate_rollout(value, budget) == expected
