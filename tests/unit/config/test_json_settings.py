"""Settings format migration preserves values, comments, layering and writes."""

import concurrent.futures
import json
import os
import tomllib
from dataclasses import fields
from pathlib import Path

import pytest

from corki.cli.resume_directory import read_resume_mode
from corki.config import CorkiPaths, CorkiSettings
from corki.config.document import loads, set_value
from corki.config.edits import add_missing_mcp_servers, set_config_value
from corki.config.migration import convert_toml


@pytest.mark.parametrize("tail", ["", ","])
def test_comments_strings_and_trailing_commas(tail):
    contents = """// leading
    { /* inline */ "url": "https://host/a/*x*/", "text": "escaped \\\" // ,}",
      "nested": {"array": [1, 2,],}, "unicode": "中文"TAIL
    } // last
    """.replace("TAIL", tail)
    value = loads(contents)
    assert value["url"] == "https://host/a/*x*/"
    assert value["text"] == 'escaped " // ,}'
    assert value["nested"]["array"] == [1, 2]
    assert value["unicode"] == "中文"


@pytest.mark.parametrize(
    "contents",
    [
        "[]",
        "null",
        '{"a":1,"a":2}',
        '{"a":NaN}',
        '{"a":Infinity}',
        "{/* unterminated",
        '{"key": "secret"',
        '{"a": 1,,}',
        "{\"a\": 'value'}",
        '[agent]\nmodel="fixture"',
    ],
)
def test_invalid_json_is_not_silently_accepted(contents):
    with pytest.raises(ValueError) as error:
        loads(contents, Path("settings.json"))
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    "source",
    ["{}", "{/* keep */}", '{"other":1}', '{"other":1, /* keep */}', '{\n  "other":1 // keep\n}'],
)
def test_edits_preserve_unrelated_comments_and_literal_keys(source, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(source)
    keys = ("plugins", 'dots.and"quotes', "tools", "calendar/list", "approval_mode")
    set_config_value(path, keys, "approve")
    set_config_value(path, keys, "deny")
    parsed = loads(path.read_text())
    assert parsed["plugins"]['dots.and"quotes']["tools"]["calendar/list"] == {
        "approval_mode": "deny"
    }
    if "other" in source:
        assert parsed["other"] == 1
    if "keep" in source:
        assert "keep" in path.read_text()
    if os.name == "nt":
        from corki.platforms.windows_acl import WindowsPrivateACL
        from corki.platforms.windows_file_handles import WindowsFiles

        files = WindowsFiles()
        handle = files.open(path, access=0x20000)
        try:
            WindowsPrivateACL().verify(handle)
        finally:
            files.close(handle)
    else:
        assert path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".corki-config-*"))


def test_edit_changes_only_selected_value():
    source = '// before\n{"tui": {"resume_cwd": "session"}, "url": "https://a"} // after\n'
    assert set_value(source, ("tui", "resume_cwd"), "current") == source.replace(
        '"session"', '"current"'
    )


@pytest.mark.parametrize("source", ['{"mcp": 1}', '{"mcp":{"servers":1}}', "{broken"])
def test_invalid_edits_leave_original_untouched(source, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(source)
    with pytest.raises(ValueError):
        add_missing_mcp_servers(path, {"new": {"command": "fixture"}})
    assert path.read_text() == source


def test_atomic_failure_and_symlink_writes(tmp_path, monkeypatch):
    target = tmp_path / "actual.json"
    target.write_text('{"value":"old"}')
    path = tmp_path / "settings.json"
    path.symlink_to(target.name)
    original = os.replace

    def fail(*args):
        raise PermissionError("fixture")

    monkeypatch.setattr("corki.config.toml_edits.os.replace", fail)
    with pytest.raises(PermissionError):
        set_config_value(path, ("value",), "new")
    assert target.read_text() == '{"value":"old"}'
    assert not list(tmp_path.glob(".corki-config-*"))
    monkeypatch.setattr("corki.config.toml_edits.os.replace", original)
    set_config_value(path, ("value",), "new")
    assert path.is_symlink()
    assert loads(target.read_text()) == {"value": "new"}


def test_concurrent_mcp_additions_and_resume_preference(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("// disabled credential retained\n{}\n")
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        jobs = [
            pool.submit(
                add_missing_mcp_servers,
                path,
                {"same": {"command": "original"}, str(n): {"command": "fixture"}},
            )
            for n in range(8)
        ]
        for job in jobs:
            job.result()
    add_missing_mcp_servers(path, {"same": {"command": "replacement"}})
    set_config_value(path, ("tui", "resume_cwd"), "session")
    assert read_resume_mode(path) == "session"
    parsed = loads(path.read_text())
    assert len(parsed["mcp"]["servers"]) == 9
    assert parsed["mcp"]["servers"]["same"]["command"] == "original"
    assert "// disabled credential retained" in path.read_text()


def test_migration_preserves_values_comments_and_runtime_settings(tmp_path):
    paths = CorkiPaths.from_home(tmp_path / "home")
    paths.home.mkdir()
    legacy = paths.home / "config.toml"
    text = """# general note
[agent]
model = "gpt-6-astra"
context_window_tokens = 524288
[provider]
# base_url = "https://old.invalid"
# api_key = "disabled-fixture-key"
base_url = "http://127.0.0.1:17001" # endpoint note
api_key = "fixture"
api_mode = "responses"
reasoning_effort = "medium"
[models.catalog."gpt-6-astra"]
context_window = 524288
truncation_policy = {mode = "tokens", limit = 10000}
[plugins]
directories = ["./plugins"]
disabled = ["fixture"]
[[skills.config]]
path = "./skills"
enabled = false
[mcp.servers."dots/slash"]
command = "fixture"
cwd = "./server"
[memories]
enabled = true
[tui]
resume_cwd = "current"
"""
    legacy.write_text(text)
    old = CorkiSettings.for_directory(tmp_path, config_file=legacy)
    paths.ensure_exists()
    assert legacy.read_text() == text
    assert loads(paths.config_file.read_text()) == tomllib.loads(text)
    for note in ("general note", "disabled-fixture-key", "https://old.invalid", "endpoint note"):
        assert note in paths.config_file.read_text()
    new = CorkiSettings.for_directory(tmp_path, config_file=paths.config_file)
    for field in fields(old):
        if field.name != "configuration":
            assert getattr(new, field.name) == getattr(old, field.name), field.name
    assert read_resume_mode(paths.config_file) == "current"
    paths.config_file.write_text('{"agent":{"model":"new"}}')
    paths.ensure_exists()
    assert loads(paths.config_file.read_text()) == {"agent": {"model": "new"}}


@pytest.mark.parametrize("trusted", [True, False])
def test_json_project_layers_and_provider_isolation(tmp_path, trusted):
    root = tmp_path / "repo"
    nested = root / "nested"
    for directory in (root / ".git", root / ".corki", nested / ".corki"):
        directory.mkdir(parents=True)
    (root / ".git/HEAD").write_text("ref: refs/heads/main\n")
    user = tmp_path / "settings.json"
    user.write_text(
        json.dumps(
            {
                "projects": {str(root): {"trust_level": "trusted" if trusted else "untrusted"}},
                "agent": {"auto_compact_tokens": 9},
                "provider": {"base_url": "https://host.invalid"},
            }
        )
    )
    (root / ".corki/settings.json").write_text('{"agent":{"auto_compact_tokens":7}}')
    (nested / ".corki/settings.json").write_text("""// project
    {"agent":{"auto_compact_tokens":5}, "provider":{"base_url":"https://bad.invalid"},}
    """)
    (nested / ".corki/config.toml").write_text("invalid legacy ignored")
    settings = CorkiSettings.for_directory(nested, config_file=user)
    assert settings.auto_compact_tokens == (5 if trusted else 9)
    assert settings.api_base == "https://host.invalid"
    assert all(layer.file.name == "settings.json" for layer in settings.configuration.layers)
    (nested / ".corki/settings.json").write_text("{invalid")
    if trusted:
        with pytest.raises(ValueError, match="Error parsing project config"):
            CorkiSettings.for_directory(nested, config_file=user)
    else:
        CorkiSettings.for_directory(nested, config_file=user)


def test_json_precedence_and_legacy_fallback(tmp_path):
    legacy = tmp_path / "config.toml"
    legacy.write_text('[agent]\nmodel="legacy"')
    path = tmp_path / "settings.json"
    assert CorkiSettings.for_directory(tmp_path, config_file=path).model == "legacy"
    path.write_text('{"agent":{"model":"json"}}')
    assert CorkiSettings.for_directory(tmp_path, config_file=path).model == "json"
    path.write_text("{invalid")
    with pytest.raises(ValueError):
        CorkiSettings.for_directory(tmp_path, config_file=path)


def test_migration_error_never_creates_partial_settings(tmp_path):
    paths = CorkiPaths.from_home(tmp_path)
    (tmp_path / "config.toml").write_text("invalid=[")
    with pytest.raises(ValueError):
        paths.ensure_exists()
    assert not paths.config_file.exists()
    assert (
        convert_toml("# comment\n")
        == "{}\n\n// Preserved notes and disabled settings from config.toml:\n// comment\n"
    )


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("new_revocation", [True, False])
def test_migration_keeps_hook_trust_and_never_overrides_revocation(
    tmp_path, enabled, new_revocation
):
    from corki.core.stop_hooks import command_identity, discover

    paths = CorkiPaths.from_home(tmp_path)
    legacy = tmp_path / "config.toml"
    fingerprint, _ = command_identity({"type": "command", "command": "echo fixture"})
    old_key = f"{legacy}:stop:0:0"
    new_key = f"{paths.config_file}:stop:0:0"
    source = (
        "[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype='command'\ncommand='echo fixture'\n"
        f"[hooks.state.{json.dumps(old_key)}]\ntrusted_hash={json.dumps(fingerprint)}\n"
        f"enabled={str(enabled).lower()}\n"
    )
    if new_revocation:
        source += f"[hooks.state.{json.dumps(new_key)}]\nenabled=false\n"
    legacy.write_text(source)
    paths.ensure_exists()
    settings = CorkiSettings.for_directory(tmp_path, config_file=paths.config_file)
    commands, warnings = discover(settings.configuration)
    assert not warnings
    assert len(commands) == (1 if enabled and not new_revocation else 0)
    if commands:
        assert commands[0].key == new_key
    assert legacy.read_text() == source


def test_table_array_notes_survive_migration():
    source = """[[models.catalog.fixture.supported_reasoning_levels]]
# first effort
 effort = "low" # inline effort
[[models.catalog.fixture.supported_reasoning_levels]]
# second effort
 effort = "high"
"""
    result = convert_toml(source)
    assert loads(result) == tomllib.loads(source)
    for comment in ("first effort", "inline effort", "second effort"):
        assert comment in result


def test_comment_only_settings_can_be_updated_and_overflow_is_rejected():
    original = "// keep\n/* another comment */"
    assert loads(original) == {}
    result = set_value(original, ("tui", "resume_cwd"), "current")
    assert original in result
    assert loads(result) == {"tui": {"resume_cwd": "current"}}
    with pytest.raises(ValueError, match="Non-finite"):
        loads('{"value": 1e9999}')
