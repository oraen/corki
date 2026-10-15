"""Host hook policy rejects malformed structure before runtime allocation."""

import json

import pytest

from corki.config import CorkiSettings
from corki.config.hooks import ManagedHookPolicy


@pytest.mark.parametrize(
    "definition",
    [
        None,
        [],
        {"Unknown": []},
        {"Stop": {}},
        {"Stop": [None]},
        {"Stop": [{}]},
        {"Stop": [{"hooks": {}}]},
        {"Stop": [{"hooks": [], "is_managed": True}]},
    ],
)
def test_invalid_policy_structure(tmp_path, definition):
    with pytest.raises(ValueError):
        ManagedHookPolicy(tmp_path / "requirements.toml", json.dumps(definition))


@pytest.mark.parametrize("value", [False, {}, "{", " " * 1_000_001])
def test_invalid_policy_json(tmp_path, value):
    with pytest.raises(ValueError):
        ManagedHookPolicy(tmp_path / "requirements.toml", value)


@pytest.mark.parametrize("value", [0, 1, "true", None])
def test_policy_boolean_is_not_coerced(tmp_path, value):
    with pytest.raises(ValueError, match="host boolean"):
        ManagedHookPolicy(tmp_path / "requirements.toml", only_managed=value)


@pytest.mark.parametrize("value", [{}, {"only_managed": True}, "requirements.toml"])
def test_settings_do_not_promote_plain_values_to_host_authority(tmp_path, value):
    with pytest.raises(ValueError, match="host-owned"):
        CorkiSettings(tmp_path, managed_hook_policy=value)
