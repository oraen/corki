import pytest

from corki.config import CorkiSettings
from corki.core.retry import retry_delay
from corki.models.backoff import backoff, retry_limit
from corki.models.failure import ModelFailure


@pytest.mark.parametrize("jitter,expected", [(0.9, 0.18), (1.0, 0.2), (1.099, 0.219)])
def test_millisecond_jitter_and_no_eight_second_cap(monkeypatch, jitter, expected):
    monkeypatch.setattr("corki.models.backoff.random.uniform", lambda low, high: jitter)
    assert backoff(0.2, 0) == expected
    assert backoff(0.2, 7) > 8
    assert (
        retry_delay(ModelFailure("rate", "rate_limit", True, 20, retry_after_seconds=1.25), 0.2)
        == 1.25
    )
    assert backoff(1e308, 100) <= (2**64 - 1) / 1000


@pytest.mark.parametrize("value", [-1, True, 1.5, "4"])
def test_adapter_retry_limit_rejects_invalid_types(value):
    with pytest.raises(ValueError):
        retry_limit(value)


@pytest.mark.parametrize(
    "document,expected",
    [
        ("", (5, 4)),
        ("[provider]\nmax_retries=2\n", (2, 4)),
        ("[provider]\nmax_retries=2\nstream_max_retries=3\nrequest_max_retries=1\n", (3, 1)),
        ("[provider]\nstream_max_retries=999\nrequest_max_retries=999\n", (100, 100)),
        ("[provider]\nstream_max_retries=0\nrequest_max_retries=0\n", (0, 0)),
    ],
)
def test_retry_config_defaults_alias_precedence_and_cap(tmp_path, document, expected):
    config = tmp_path / "config.toml"
    config.write_text(document)
    settings = CorkiSettings.for_directory(tmp_path, config_file=config)
    assert (settings.model_max_retries, settings.model_request_max_retries) == expected
    assert settings.model_retry_base_seconds == 0.2


@pytest.mark.parametrize("value", ["-1", "true", "1.5", "'4'"])
def test_request_retry_config_rejects_invalid_values(tmp_path, value):
    config = tmp_path / "config.toml"
    config.write_text(f"[provider]\nrequest_max_retries={value}\n")
    with pytest.raises(ValueError, match="retries"):
        CorkiSettings.for_directory(tmp_path, config_file=config)
