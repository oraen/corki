import pytest

from corki.config.model_context import parse_model_contexts


@pytest.mark.parametrize("value", [False, 1, {}, "x" * 30_001])
def test_local_model_instructions_reject_invalid_or_oversized_values(value):
    with pytest.raises(ValueError, match="base_instructions"):
        parse_model_contexts({"fixture": {"base_instructions": value}})


@pytest.mark.parametrize("value", [None, "", " retained \n", "x" * 30_000])
def test_local_model_instructions_preserve_valid_values(value):
    catalog = parse_model_contexts({"fixture": {"base_instructions": value}})
    assert catalog[0].base_instructions == value
