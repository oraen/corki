import json
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.config import permissions as policies
from corki.config.instructions import ProjectInstructionsConfig


@pytest.mark.parametrize("trust", [None, "trusted", "untrusted"])
def test_sdk_default_captures_selection_without_leaking_marker(tmp_path, monkeypatch, trust):
    compiler = tmp_path / "host-compiler"
    monkeypatch.setattr(policies, "bundled_compiler", lambda: compiler)
    settings = CorkiSettings(
        working_directory=tmp_path,
        project_instructions=ProjectInstructionsConfig(trust_level=trust),
    )
    value = settings.execution_permissions
    assert isinstance(value, policies.ExecutionPermissions)
    assert value.compiler == compiler
    assert value.policy_cwd == tmp_path.resolve()
    assert json.loads(value.profile_json) == {
        "type": "selection",
        **({"project_trust": trust} if trust is not None else {}),
    }
    monkeypatch.setattr(policies, "bundled_compiler", lambda: pytest.fail("must not reselect"))
    assert replace(settings, model="another-model").execution_permissions == value


@pytest.mark.parametrize("entry", ["sdk", "config"])
def test_missing_default_backend_is_not_unrestricted(tmp_path, monkeypatch, entry):
    monkeypatch.setattr(policies, "bundled_compiler", lambda: None)
    with pytest.raises(ValueError, match="no bundled compiler"):
        if entry == "sdk":
            CorkiSettings(working_directory=tmp_path)
        else:
            CorkiSettings.for_directory(tmp_path)


def test_explicit_host_control_is_distinct_from_omitted_sdk_default(tmp_path, monkeypatch):
    monkeypatch.setattr(policies, "bundled_compiler", lambda: pytest.fail("host owns execution"))
    settings = CorkiSettings(working_directory=tmp_path, execution_permissions=None)
    assert settings.execution_permissions is None
    assert replace(settings, model="another-model").execution_permissions is None


def test_default_template_does_not_block_explicit_compiler_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(policies, "bundled_compiler", lambda: pytest.fail("explicit override"))
    config = tmp_path / "config.toml"
    config.write_text('[execution]\ncompiler="/host/compiler"\nprofile={type="disabled"}')
    settings = CorkiSettings.for_directory(tmp_path, config_file=config)
    assert json.loads(settings.execution_permissions.profile_json) == {"type": "disabled"}
    assert settings.execution_permissions.approval_policy_json == '"on-request"'
