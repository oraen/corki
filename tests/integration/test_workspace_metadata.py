"""Product metadata must inherit native defaults, overrides and OS enforcement."""

import asyncio
import json
import shlex
import sys

import pytest
from test_bundled_execution import compiler as compiler
from test_execution_permissions import _run
from test_execution_policy_live_inheritance import Model, run

from corki.config import CorkiSettings
from corki.config.instructions import ProjectInstructionsConfig
from corki.config.permissions import ExecutionPermissions
from corki.core import LangGraphRuntime
from corki.execution.backend import resolve_execution_permissions
from corki.protocol.items import ToolResultItem

NAMES = (".codex", ".corki")
WORKSPACE = {
    "type": "workspace-write",
    "exclude_tmpdir_env_var": True,
    "exclude_slash_tmp": True,
}


def command(actions):
    script = f"""
from pathlib import Path
result = {{}}
for label, operation, raw in {actions!r}:
    path = Path(raw)
    try:
        if operation == 'write':
            path.write_text('model write')
        elif operation == 'mkdir':
            path.mkdir()
        elif operation == 'unlink':
            path.unlink()
        elif operation == 'rename':
            path.rename(path.with_name(path.name + '-moved'))
        elif operation == 'read':
            path.read_text()
        else:
            raise AssertionError(operation)
    except PermissionError:
        result[label] = False
    else:
        result[label] = True
encoded = ','.join(k + '=' + str(v) for k, v in sorted(result.items()))
print('METADATA_RESULT=' + encoded, flush=True)
"""
    return {"cmd": shlex.join([sys.executable, "-I", "-c", script]), "login": False}


def assert_results(results, expected):
    marker = "METADATA_RESULT=" + ",".join(k + "=" + str(v) for k, v in sorted(expected.items()))
    assert any(marker in item.content for item in results), [item.content for item in results]


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize(
    "layout", ["existing", "missing", "internal-link", "external-link", "external-grant-link"]
)
def test_workspace_metadata_names_have_same_real_enforcement(tmp_path, compiler, nested, layout):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    actions, expected = [], {"ordinary": True}
    external_grant = layout == "external-grant-link"
    for name in NAMES:
        metadata = workspace / name
        if layout == "missing":
            actions.append((name, "mkdir", str(metadata)))
        else:
            if layout.endswith("link"):
                target = (outside if layout.startswith("external") else workspace) / (
                    name + "-target"
                )
                target.mkdir()
                metadata.symlink_to(target, target_is_directory=True)
                actions.append((name + "-target", "write", str(target / "config.toml")))
                expected[name + "-target"] = external_grant
                for op in ("unlink", "rename"):
                    actions.append((name + "-" + op, op, str(metadata)))
                    expected[name + "-" + op] = False
            else:
                metadata.mkdir()
            (metadata / "config.toml").write_text("# original")
            actions.insert(0, (name, "write", str(metadata / "config.toml")))
        expected[name] = external_grant
    actions.append(("ordinary", "write", str(workspace / "ordinary")))
    policy = ExecutionPermissions(
        compiler,
        workspace,
        json.dumps({**WORKSPACE, "writable_roots": [str(outside)] if external_grant else []}),
    )
    results = asyncio.run(_run(workspace, policy, "exec_command", command(actions), nested))
    assert_results(results, expected)
    for name in NAMES:
        metadata = workspace / name
        if layout == "missing":
            assert not metadata.exists()
        else:
            assert (metadata / "config.toml").read_text() == (
                "model write" if external_grant else "# original"
            )
            assert metadata.is_symlink() is layout.endswith("link")


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_default_sdk_protects_own_metadata_without_explicit_policy(tmp_path, compiler, mode):
    async def scenario():
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        for name in NAMES:
            (workspace / name).mkdir()
            (workspace / name / "config.toml").write_text("# original")
        model = Model(mode)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=workspace,
                skills_enabled=False,
                tool_mode=mode,
                project_instructions=ProjectInstructionsConfig(trust_level="trusted"),
            ),
            model=model,
            database_path=tmp_path / "state.db",
            home_path=tmp_path / "host",
        )
        try:
            await run(
                runtime,
                model,
                command([(n, "write", str(workspace / n / "config.toml")) for n in NAMES]),
            )
            results = [i for i in model.requests[-1].items if isinstance(i, ToolResultItem)]
            assert_results(results, dict.fromkeys(NAMES, False))
            assert runtime._settings.execution_permissions.active_profile.id == ":workspace"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def managed_profile(workspace, overrides):
    return {
        "type": "managed",
        "network": "restricted",
        "file_system": {
            "type": "restricted",
            "entries": [
                {"access": "read", "path": {"type": "special", "value": {"kind": "root"}}},
                {"access": "write", "path": {"type": "path", "path": str(workspace)}},
                *overrides,
            ],
        },
    }


@pytest.mark.parametrize("access", ["write", "read", "deny", "child-write"])
@pytest.mark.parametrize("symbolic", [False, True])
def test_explicit_metadata_rules_keep_native_precedence(tmp_path, compiler, access, symbolic):
    actions, expected, overrides = [], {}, []
    for name in NAMES:
        metadata = tmp_path / name
        metadata.mkdir()
        (metadata / "config.toml").write_text("# original")
        subpath = name + "/allowed" if access == "child-write" else name
        if access == "child-write":
            (metadata / "allowed").mkdir()
        path = (
            {"type": "special", "value": {"kind": "project_roots", "subpath": subpath}}
            if symbolic
            else {"type": "path", "path": str(tmp_path / subpath)}
        )
        overrides.append({"path": path, "access": "write" if access == "child-write" else access})
        actions.extend(
            [
                (name + "-read", "read", str(metadata / "config.toml")),
                (name + "-write", "write", str(metadata / "config.toml")),
            ]
        )
        expected[name + "-read"] = access != "deny"
        expected[name + "-write"] = access == "write"
        if access == "child-write":
            actions.append((name + "-child", "write", str(metadata / "allowed/file")))
            expected[name + "-child"] = True
    policy = ExecutionPermissions(
        compiler, tmp_path, json.dumps(managed_profile(tmp_path, overrides))
    )
    results = asyncio.run(_run(tmp_path, policy, "exec_command", command(actions), False))
    assert_results(results, expected)


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_patch_helper_cannot_create_or_rewrite_metadata(tmp_path, compiler, nested, existing):
    metadata = tmp_path / ".corki"
    if existing:
        metadata.mkdir()
    policy = ExecutionPermissions(compiler, tmp_path, json.dumps(WORKSPACE))
    results = asyncio.run(
        _run(
            tmp_path,
            policy,
            "apply_patch",
            {
                "patch": (
                    "*** Begin Patch\n*** Add File: .corki/model.toml\n+forbidden\n*** End Patch"
                )
            },
            nested,
        )
    )
    assert not (metadata / "model.toml").exists(), results
    # The native core safety gate rejects the whole patch before an OS write.
    # This is a stronger boundary than waiting for a permission-denied write.
    assert any("patch rejected:" in item.content for item in results), [
        item.content for item in results
    ]


@pytest.mark.parametrize(
    "profile", [{"type": "disabled"}, {"type": "external", "network": "enabled"}]
)
def test_native_unrestricted_modes_are_not_silently_restricted(tmp_path, compiler, profile):
    policy = ExecutionPermissions(compiler, tmp_path, json.dumps(profile))
    results = asyncio.run(
        _run(
            tmp_path,
            policy,
            "exec_command",
            command([(name, "mkdir", str(tmp_path / name)) for name in NAMES]),
            False,
        )
    )
    assert_results(results, dict.fromkeys(NAMES, True))


@pytest.mark.parametrize("layout", ["extra-root", "root-alias", "extra-root-alias", "system-alias"])
@pytest.mark.parametrize("existing", [False, True])
def test_metadata_protection_follows_native_writable_root_aliases(
    tmp_path, compiler, layout, existing
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    if layout == "root-alias":
        workspace = alias
    actions = []
    for name in NAMES:
        path = real / name
        if existing:
            path.mkdir()
            path = path / "config.toml"
            path.write_text("# original")
        actions.append((name, "write" if existing else "mkdir", str(path)))
    writable = alias if layout.endswith("alias") and layout != "system-alias" else real
    if layout == "system-alias":
        assert str(real).startswith("/private/var/")
        writable = str(real).removeprefix("/private")
    policy = ExecutionPermissions(
        compiler, workspace, json.dumps({**WORKSPACE, "writable_roots": [str(writable)]})
    )
    # Native Seatbelt rejects attacker-mutable writable roots, while allowing
    # system /var -> /private/var aliases. Do not normalize away this admission.
    if layout in {"root-alias", "extra-root-alias"}:
        with pytest.raises(ValueError, match="symlinked writable roots are not supported"):
            asyncio.run(_run(workspace, policy, "exec_command", command(actions), False))
        for name in NAMES:
            if existing:
                assert (real / name / "config.toml").read_text() == "# original"
            else:
                assert not (real / name).exists()
        return
    results = asyncio.run(_run(workspace, policy, "exec_command", command(actions), False))
    assert_results(results, dict.fromkeys(NAMES, False))


def test_metadata_defaults_do_not_add_read_grants_to_deny_only_profile(tmp_path, compiler):
    entries = [
        {"access": "deny", "path": {"type": "path", "path": str(tmp_path / name)}} for name in NAMES
    ]
    profile = {
        "type": "managed",
        "network": "restricted",
        "file_system": {"type": "restricted", "entries": entries},
    }
    admitted, warnings = asyncio.run(
        resolve_execution_permissions(ExecutionPermissions(compiler, tmp_path, json.dumps(profile)))
    )
    assert not warnings
    actual = json.loads(admitted.profile_json)["file_system"]["entries"]
    assert len(actual) == 2
    assert all(entry["access"] == "deny" for entry in actual)
