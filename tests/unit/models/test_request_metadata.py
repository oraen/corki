"""Provider identity and shallow, non-mutating filtering of typed item metadata."""

from copy import deepcopy

import pytest

from corki.config import CorkiSettings
from corki.models import resolve_capabilities
from corki.models.request_metadata import METADATA, filter_request_metadata
from corki.models.types import ModelRequest


@pytest.mark.parametrize(
    "name,base,expected",
    [
        ("openai", "https://fixture.invalid/v1", False),
        ("OpenAI", "https://fixture.invalid/v1", False),
        (None, "https://api.openai.com/v1", False),
        ("custom", "https://api.openai.com/v1", False),
        ("azure", "https://api.openai.com/v1", False),
        (None, "https://example.openai.azure.com/openai", False),
        (None, "https://fixture.invalid/v1", False),
    ],
)
def test_private_metadata_capability_is_not_generic_responses_or_azure(name, base, expected):
    cap = resolve_capabilities(base_url=base, api_mode="responses", provider_name=name)
    assert cap.supports_internal_metadata == expected


@pytest.mark.parametrize("supported", [False, True])
@pytest.mark.parametrize("kinds", [False, True])
@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"content_item_kinds": ["user.text"]},
        {
            "content_item_kinds": ["user.text"],
            "turn_id": "t",
            "create_time": 1,
            "cell_id": "owned-cell",
        },
    ],
)
def test_request_filter_preserves_other_fields_and_original_data(supported, kinds, metadata):
    items = [
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call",
            "name": "lookup",
            "encrypted_function_args": ["private"],
            "arguments": {METADATA: "ordinary-data"},
            METADATA: metadata,
        }
    ]
    original = deepcopy(items)
    result = filter_request_metadata(items, supported=supported, content_item_kinds=kinds)
    assert items == original
    assert result[0]["arguments"] == {METADATA: "ordinary-data"}
    assert result[0]["id"] == "fc_1" and result[0]["call_id"] == "call"
    assert "encrypted_function_args" not in result[0]
    assert METADATA not in result[0]


@pytest.mark.parametrize("bad", [None, 0, 1, "false", [], {}])
def test_content_kind_feature_requires_boolean(tmp_path, bad):
    with pytest.raises(ValueError, match="content_item_kinds"):
        CorkiSettings(tmp_path, content_item_kinds=bad)
    with pytest.raises(ValueError, match="content_item_kinds"):
        ModelRequest("model", "base", (), (), (), content_item_kinds=bad)


def test_default_enabled_and_toml_feature_disable(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("[features]\ncontent_item_kinds=false\n")
    assert CorkiSettings(tmp_path).content_item_kinds is True
    assert CorkiSettings.for_directory(tmp_path, config_file=config).content_item_kinds is False
