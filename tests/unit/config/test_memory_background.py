"""Explicit host pause is separate from Codex-compatible source eligibility."""

import pytest

from corki.config import CorkiSettings


@pytest.mark.parametrize("value", ["true", "false"])
def test_memory_background_setting_loads_independently(tmp_path, value):
    config = tmp_path / "config.toml"
    config.write_text(
        f"[memories]\nenabled = true\ngenerate = false\nbackground_enabled = {value}\n"
    )
    settings = CorkiSettings.for_directory(tmp_path, config_file=config)
    assert settings.memories_background_enabled is (value == "true")
    assert settings.memories_generate is False
    assert settings.memories_use is True


@pytest.mark.parametrize("value", ["'false'", "0", "[]"])
def test_memory_background_setting_rejects_non_booleans(tmp_path, value):
    config = tmp_path / "config.toml"
    config.write_text(f"[memories]\nbackground_enabled = {value}\n")
    with pytest.raises(ValueError, match="memories.background_enabled"):
        CorkiSettings.for_directory(tmp_path, config_file=config)
