import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from corki.config import CorkiSettings
from corki.config.permissions import ExecutionPermissions, parse_execution_permissions


def test_explicit_configuration_binds_host_workspace_not_model_cwd(tmp_path):
    config = tmp_path / "config.toml"
    compiler = tmp_path / "host-bin" / "compiler"
    config.write_text(
        "[execution]\ncompiler = "
        + json.dumps(str(compiler))
        + '\nprofile = {type="workspace-write", network_access=false}\n'
    )
    settings = CorkiSettings.for_directory(tmp_path, config_file=config)
    permissions = settings.execution_permissions
    assert permissions.compiler == compiler
    assert permissions.policy_cwd == tmp_path.resolve()
    assert json.loads(permissions.profile_json) == {
        "type": "workspace-write",
        "network_access": False,
    }
    with pytest.raises(FrozenInstanceError):
        permissions.policy_cwd = tmp_path / "model-cwd"


@pytest.mark.parametrize(
    "value",
    [
        False,
        [],
        {},
        {"compiler": "/x"},
        {"compiler": "relative", "profile": {"type": "disabled"}},
        {"compiler": "/x", "profile": None},
    ],
)
def test_invalid_explicit_configuration_never_becomes_missing_policy(tmp_path, value):
    with pytest.raises(ValueError):
        parse_execution_permissions(value, tmp_path)


def test_absent_policy_selects_native_defaults(tmp_path):
    assert CorkiSettings.for_directory(tmp_path).execution_permissions.needs_resolution
    with pytest.raises(ValueError):
        CorkiSettings(working_directory=tmp_path, execution_permissions={"type": "disabled"})


@pytest.mark.parametrize("profile", ["{", "[]", "null", '{"type":5}'])
def test_invalid_profile_shape_is_rejected_before_runtime(tmp_path, profile):
    with pytest.raises(ValueError):
        ExecutionPermissions(Path("/host/compiler"), tmp_path, profile)


@pytest.mark.parametrize("policy", ["never", "on-request", "on-failure"])
def test_configured_shell_approval_is_not_mcp_approval(tmp_path, policy):
    configuration = {"approval_policy": policy}
    value = {"compiler": "/host/compiler", "profile": {"type": "read-only"}}
    permissions = parse_execution_permissions(value, tmp_path, configuration=configuration)
    assert json.loads(permissions.approval_policy_json) == (
        "on-request" if policy == "on-failure" else policy
    )
    assert (
        CorkiSettings(
            working_directory=tmp_path, execution_permissions=permissions
        ).mcp_approval_policy
        == "never"
    )


@pytest.mark.parametrize(
    "configuration", [{"approval_policy": "on-request"}, {"approval_policy": "untrusted"}]
)
def test_approval_configuration_cannot_fall_into_unconfigured_execution(
    tmp_path, configuration, monkeypatch
):
    from corki.execution import bundled

    monkeypatch.setattr(bundled, "_BUNDLE", tmp_path / "installation-without-compiler")
    with pytest.raises(ValueError):
        parse_execution_permissions(None, tmp_path, configuration=configuration)
    if configuration["approval_policy"] == "untrusted":
        with pytest.raises(ValueError, match="no longer supported"):
            parse_execution_permissions(
                {"compiler": "/host/compiler"}, tmp_path, configuration=configuration
            )


@pytest.mark.parametrize(
    "policy",
    [
        False,
        "always",
        [],
        {"granular": {}},
        {"granular": {"rules": 1, "sandbox_approval": True, "mcp_elicitations": True}},
    ],
)
def test_malformed_shell_approval_never_becomes_never_or_allow(tmp_path, policy):
    with pytest.raises(ValueError):
        ExecutionPermissions(
            Path("/host/compiler"),
            tmp_path,
            '{"type":"read-only"}',
            approval_policy_json=json.dumps(policy),
        )


def test_granular_policy_ignores_unknown_fields_but_rejects_typed_duplicates(tmp_path):
    raw = '{"granular":{"rules":true,"sandbox_approval":false,"mcp_elicitations":false'
    permission = ExecutionPermissions(
        Path("/host/compiler"),
        tmp_path,
        '{"type":"read-only"}',
        approval_policy_json=raw + ',"unknown":{"anything":1}}}',
    )
    assert json.loads(permission.approval_policy_json) == {
        "granular": {
            "rules": True,
            "sandbox_approval": False,
            "mcp_elicitations": False,
            "skill_approval": False,
            "request_permissions": False,
        }
    }
    with pytest.raises(ValueError, match="duplicate field"):
        ExecutionPermissions(
            Path("/host/compiler"),
            tmp_path,
            '{"type":"read-only"}',
            approval_policy_json=raw + ',"rules":false}}',
        )
