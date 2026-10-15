"""Old account-service settings are inert, not a route to official services."""

import pytest

from corki.config import CorkiSettings


@pytest.mark.parametrize(
    "address", ["https://chatgpt.com/backend-api", "https://accounts.example.test"]
)
def test_legacy_account_configuration_is_not_loaded(tmp_path, address):
    config = tmp_path / "config.toml"
    config.write_text(
        f'[provider]\ncodex_backend=true\ncodex_account_base_url="{address}"\n'
        "[memories]\nmin_rate_limit_remaining_percent=100\n"
    )
    settings = CorkiSettings.for_directory(tmp_path, config_file=config)
    assert not hasattr(settings, "codex_account_base_url")
    assert not hasattr(settings, "memories_min_rate_limit_remaining_percent")
