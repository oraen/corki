"""Layer ordering, source-relative paths and disabled-layer error semantics."""

import json
from pathlib import Path

import pytest
from test_project_trust_selection import worktree

from corki.config import CorkiSettings, project_trust
from corki.config.layers import load_local_config


def project(tmp_path, trust="trusted"):
    root = tmp_path / "repo"
    nested = root / "nested"
    for folder in (root / ".git", root / ".corki", nested / ".corki", tmp_path / "home"):
        folder.mkdir(parents=True)
    (root / ".git/HEAD").write_text("ref: refs/heads/main\n")
    user = tmp_path / "home/config.toml"
    user.write_text(
        "" if trust is None else f'[projects.{json.dumps(str(root))}]\ntrust_level="{trust}"\n'
    )
    return root, nested, user


def test_hierarchy_arrays_aliases_and_empty_cwd_trust(tmp_path):
    root, cwd, user = project(tmp_path)
    user.write_text(
        user.read_text() + f"[projects.{json.dumps(str(cwd))}]\n"
        "[agent]\nmax_steps=9\n[memories]\ndisable_on_external_context=false\n"
    )
    (root / ".corki/config.toml").write_text('[agent]\nmax_steps=4\n[plugins]\ndisabled=["root"]\n')
    (cwd / ".corki/config.toml").write_text(
        '[agent]\nmax_steps=7\n[plugins]\ndisabled=["nested"]\n[memories]\nno_memories_if_mcp_or_web_search=true\n'
    )
    settings = CorkiSettings.for_directory(cwd, config_file=user)
    assert settings.max_steps == 7
    assert settings.disabled_plugins == frozenset({"nested"})
    assert settings.memories_disable_on_external_context is True
    # Empty cwd masks active-project trust, but not the layer gate's root trust.
    assert settings.project_instructions.trust_level is None
    assert settings.configuration.rule_folders(tmp_path) == (
        user.parent,
        root / ".corki",
        cwd / ".corki",
    )


def test_nested_explicit_untrusted_overrides_root_only_for_its_layer(tmp_path):
    root, cwd, user = project(tmp_path)
    user.write_text(
        user.read_text() + f'[projects.{json.dumps(str(cwd))}]\ntrust_level="untrusted"\n'
    )
    (root / ".corki/config.toml").write_text("[agent]\nmax_steps=4\n")
    (cwd / ".corki/config.toml").write_text('[agent]\nmax_steps="invalid"\n')
    settings = CorkiSettings.for_directory(cwd, config_file=user)
    assert settings.max_steps == 4
    assert settings.configuration.rule_folders(tmp_path) == (user.parent, root / ".corki")
    assert settings.configuration.layers[-1].disabled_reason is not None


def test_untrusted_source_cannot_admit_itself_or_descendants(tmp_path):
    root, cwd, user = project(tmp_path, None)
    (root / ".corki/config.toml").write_text(
        f'[projects.{json.dumps(str(root))}]\ntrust_level="trusted"\n'
    )
    (cwd / ".corki/config.toml").write_text("[agent]\nmax_steps=3\n")
    settings = CorkiSettings.for_directory(cwd, config_file=user)
    assert settings.max_steps is None
    assert settings.project_instructions.trust_level is None
    assert settings.configuration.rule_folders(tmp_path) == (user.parent,)
    assert len(settings.configuration.warnings) == 2


@pytest.mark.parametrize("trust", [None, "trusted", "untrusted"])
def test_syntax_errors_are_fatal_only_in_trusted_project_layers(tmp_path, trust):
    root, cwd, user = project(tmp_path, trust)
    (root / ".corki/config.toml").write_text("not valid TOML !")
    if trust == "trusted":
        with pytest.raises(ValueError, match="Error parsing project config"):
            CorkiSettings.for_directory(cwd, config_file=user)
    else:
        settings = CorkiSettings.for_directory(cwd, config_file=user)
        assert settings.configuration.layers[1].disabled_reason is not None


@pytest.mark.parametrize("trust", [None, "trusted", "untrusted"])
def test_unreadable_project_config_is_not_treated_as_absent(tmp_path, monkeypatch, trust):
    root, cwd, user = project(tmp_path, trust)
    original = Path.read_text

    def read(path, *args, **kwargs):
        if path == root / ".corki/config.toml":
            raise PermissionError("fixture denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(PermissionError, match="fixture denied"):
        CorkiSettings.for_directory(cwd, config_file=user)


def test_user_home_alias_is_not_reloaded_as_project_config(tmp_path):
    root, cwd, user = project(tmp_path)
    (root / ".corki").rmdir()
    (root / ".corki").symlink_to(user.parent, target_is_directory=True)
    _, sources = load_local_config(cwd, user)
    assert [layer.file for layer in sources.layers] == [user, cwd / ".corki/config.toml"]


def test_paths_are_resolved_in_the_declaring_layer(tmp_path):
    root, cwd, user = project(tmp_path)
    user.write_text(
        user.read_text() + '[plugins]\ndirectories=["./user-plugin"]\n'
        '[[skills.config]]\npath="./skills"\nenabled=false\n'
    )
    (root / ".corki/config.toml").write_text(
        '[[skills.config]]\npath="../skills"\nenabled=false\n[mcp.servers.fixture]\ncommand="echo"\ncwd="./server"\n'
    )
    settings = CorkiSettings.for_directory(cwd, config_file=user)
    assert settings.plugin_dirs == (user.parent / "user-plugin",)
    assert settings.skills_config[0].path == user.parent / "skills"
    assert settings.mcp_servers[0].cwd == root / ".corki/server"


def test_project_cannot_redirect_provider_or_execution_compiler(tmp_path):
    root, cwd, user = project(tmp_path)
    user.write_text(
        user.read_text()
        + '[provider]\nbase_url="https://host.invalid/v1"\n[execution]\ncompiler="/host/compiler"\nprofile={type="read-only"}\n'
    )
    (root / ".corki/config.toml").write_text(
        '[provider]\nbase_url="https://repository.invalid"\n[execution]\ncompiler="/repository/compiler"\n[agent]\nmax_steps=5\n'
    )
    settings = CorkiSettings.for_directory(cwd, config_file=user)
    assert settings.api_base == "https://host.invalid/v1"
    assert settings.execution_permissions.compiler == Path("/host/compiler")
    assert settings.max_steps == 5
    assert any(
        "execution.compiler" in warning and "provider" in warning
        for warning in settings.configuration.warnings
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_switching_filter_representation_removes_lower_form(tmp_path, reverse):
    root, cwd, user = project(tmp_path)
    legacy = '[shell_environment_policy]\nexclude=["DROP"]\n'
    keyed = '[shell_environment_policy.filters]\nKEEP="include"\n'
    lower, higher = (keyed, legacy) if reverse else (legacy, keyed)
    user.write_text(user.read_text() + lower)
    (root / ".corki/config.toml").write_text(higher)
    policy = CorkiSettings.for_directory(cwd, config_file=user).shell_environment_policy
    assert policy.exclude == (("DROP",) if reverse else ())
    assert policy.include_only == (() if reverse else ("KEEP",))


def test_filters_merge_case_insensitively_but_validate_each_source(tmp_path):
    root, cwd, user = project(tmp_path)
    user.write_text(user.read_text() + '[shell_environment_policy.filters]\nKEY="include"\n')
    layer = root / ".corki/config.toml"
    layer.write_text('[shell_environment_policy.filters]\nkey="exclude"\n')
    settings = CorkiSettings.for_directory(cwd, config_file=user)
    assert settings.shell_environment_policy.exclude == ("key",)
    assert settings.shell_environment_policy.include_only == ()
    layer.write_text('[shell_environment_policy.filters]\nkey="include"\nKEY="exclude"\n')
    (cwd / ".corki/config.toml").write_text("[shell_environment_policy]\nexclude=[]\n")
    with pytest.raises(ValueError, match="duplicate"):
        CorkiSettings.for_directory(cwd, config_file=user)


def test_layer_lookup_does_not_apply_active_project_wsl_folding(monkeypatch):
    path = Path("/mnt/C/Project")
    monkeypatch.setattr(project_trust.sys, "platform", "linux")
    monkeypatch.setenv("WSL_DISTRO_NAME", "fixture")
    monkeypatch.setattr(project_trust, "_canonical", lambda _: path)
    projects = {
        str(path): {"trust_level": "trusted"},
        "/mnt/c/project": {"trust_level": "untrusted"},
    }
    assert project_trust.lookup_project(projects, path) == {"trust_level": "untrusted"}
    assert project_trust.lookup_project(projects, path, normalize_wsl=False) == {
        "trust_level": "trusted"
    }


@pytest.mark.parametrize("marker", ["custom", "disabled", "empty_git"])
def test_project_layer_search_obeys_configured_root_boundaries(tmp_path, marker):
    root, cwd, user = project(tmp_path)
    (root / ".corki/config.toml").write_text("[agent]\nmax_steps=3\n")
    if marker == "custom":
        (root / "marker").write_text("")
        user.write_text('project_root_markers=["marker"]\n' + user.read_text())
    elif marker == "disabled":
        user.write_text("project_root_markers=[]\n" + user.read_text())
    else:
        (cwd / ".git").mkdir()
    settings = CorkiSettings.for_directory(cwd, config_file=user)
    assert settings.max_steps == (None if marker == "disabled" else 3)


@pytest.mark.parametrize("valid", [False, True])
def test_worktree_layer_requires_proven_main_checkout_trust(tmp_path, valid):
    main, checkout, registration = worktree(tmp_path)
    folder = checkout / ".corki"
    folder.mkdir()
    (folder / "config.toml").write_text("[agent]\nmax_steps=3\n")
    user = tmp_path / "user.toml"
    user.write_text(f'[projects.{json.dumps(str(main))}]\ntrust_level="trusted"\n')
    if not valid:
        (registration / "gitdir").write_text(str(main / ".git"))
    settings = CorkiSettings.for_directory(checkout, config_file=user)
    assert settings.max_steps == (3 if valid else None)
