import pytest

from corki.models import ModelError
from corki.models.response_completion import parse_response_completion
from corki.protocol.wire_json import NUMBER_KEY


@pytest.mark.parametrize("value", [-(2**63), -1, 0, 2**63 - 1])
def test_signed_i64_usage_and_optional_null_details(value):
    result = parse_response_completion(
        {
            "id": "",
            "usage": {
                "input_tokens": value,
                "output_tokens": value,
                "total_tokens": value,
                "input_tokens_details": None,
                "output_tokens_details": None,
            },
        }
    )
    assert (
        result.usage.input_tokens
        == result.usage.output_tokens
        == result.usage.total_tokens
        == value
    )
    assert (
        result.usage.cached_tokens
        == result.usage.cache_write_tokens
        == result.usage.reasoning_tokens
        == 0
    )
    assert result.provider_metadata["response_id"] == ""


@pytest.mark.parametrize("value", [True, False, None, "0", 0.0, -(2**63) - 1, 2**63])
def test_cache_write_default_only_applies_to_absent_field(value):
    with pytest.raises(ModelError):
        parse_response_completion(
            {
                "id": "r",
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": value},
                },
            }
        )


@pytest.mark.parametrize(
    "value", [None, {}, {"amount": "", "metadata": [1, None]}, {"unknown": False}]
)
def test_private_usage_metadata_is_ignored_without_inventing_counts(value):
    result = parse_response_completion({"id": "r", "usage": None, "usage_metadata": value})
    assert result.usage.total_tokens is None
    assert result.provider_metadata == {"response_id": "r"}


@pytest.mark.parametrize("budget", [False, "0", [], {}, {NUMBER_KEY: "x"}])
def test_private_rollout_budget_does_not_control_ordinary_usage(budget):
    result = parse_response_completion(
        {
            "id": "r",
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "codex_rollout_budget_units": budget,
            },
        }
    )
    assert result.usage.codex_rollout_budget_units is None
    assert result.usage.total_tokens == 0


def test_default_cache_write_and_private_number_budget():
    result = parse_response_completion(
        {
            "id": "r",
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "input_tokens_details": {"cached_tokens": 0},
                "codex_rollout_budget_units": {NUMBER_KEY: "1e999"},
            },
        }
    )
    assert result.usage.cache_write_tokens == 0
    assert result.usage.codex_rollout_budget_units is None
