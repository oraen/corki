import json
from dataclasses import FrozenInstanceError

import pytest

from corki.config import CorkiSettings
from corki.config.permissions import ActivePermissionProfile, ExecutionPermissions


@pytest.mark.parametrize(
    "configuration",
    [
        'default_permissions=":read-only"',
        '[permissions.build]\nextends=":workspace"',
    ],
)
def test_named_configuration_without_compiler_is_not_silently_ignored(
    tmp_path, configuration, monkeypatch
):
    from corki.execution import bundled

    monkeypatch.setattr(bundled, "_BUNDLE", tmp_path / "installation-without-compiler")
    path = tmp_path / "config.toml"
    path.write_text(configuration)
    with pytest.raises(ValueError, match="no bundled compiler"):
        CorkiSettings.for_directory(tmp_path, config_file=path)


def test_raw_and_named_sources_cannot_be_combined(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        'default_permissions=":read-only"\n[execution]\ncompiler="/host/compiler"\nprofile={type="disabled"}'
    )
    with pytest.raises(ValueError, match="cannot be combined"):
        CorkiSettings.for_directory(tmp_path, config_file=path)


def test_compiler_only_configuration_freezes_implicit_selection(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[execution]\ncompiler="/host/compiler"')
    permissions = CorkiSettings.for_directory(tmp_path, config_file=path).execution_permissions
    assert permissions.needs_resolution
    assert json.loads(permissions.profile_json) == {"type": "selection"}
    path.write_text('default_permissions=":danger-full-access"')
    assert json.loads(permissions.profile_json) == {"type": "selection"}


def test_active_identity_and_roots_are_immutable(tmp_path):
    profile = ActivePermissionProfile("build", ":workspace")
    roots = [tmp_path / "extra"]
    permissions = ExecutionPermissions(
        tmp_path / "compiler",
        tmp_path,
        '{"type":"managed"}',
        active_profile=profile,
        profile_workspace_roots=roots,
    )
    roots.clear()
    assert permissions.profile_workspace_roots == (tmp_path / "extra",)
    with pytest.raises(FrozenInstanceError):
        profile.id = "changed"
