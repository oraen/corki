"""Provider-scoped model metadata and atomic default persistence."""

import json
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.config.document import loads
from corki.config.edits import save_model_default
from corki.protocol.settings import ThreadModelSettings


@pytest.fixture
def configured(tmp_path, monkeypatch):
    for key in ("CORKI_MODEL", "CORKI_API_BASE", "CORKI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / "settings.json"
    document = {
        "default": {"provider": "a", "model": "shared"},
        "tools": {"command_timeout_seconds": 123},
        "providers": {
            "a": {
                "base_url": "https://a.invalid",
                "api_key": "key-a",
                "api_mode": "responses",
                "aliases": ["default"],
                "models": {
                    "shared": {"context_window": 524288, "reasoning_effort": "medium"},
                    "z": {},
                },
            },
            "b": {
                "base_url": "https://b.invalid",
                "api_key": "key-b",
                "models": {
                    "shared": {"context_window": 65536, "reasoning_effort": "high"},
                    "aaa": {},
                },
            },
        },
    }
    path.write_text("// keep credentials and comments\n" + json.dumps(document))
    return path, CorkiSettings.for_directory(tmp_path, config_file=path)


def test_selection_scopes_transport_metadata_and_keeps_runtime_policy(configured):
    path, settings = configured
    assert (settings.provider_id, settings.model, settings.reasoning_effort) == (
        "a",
        "shared",
        "medium",
    )
    other = replace(settings, command_timeout_seconds=999).for_model("b", "shared")
    assert (other.api_base, other.api_key, other.api_mode) == (
        "https://b.invalid",
        "key-b",
        "chat_completions",
    )
    assert other.main_context_limits.raw_tokens == 65536
    assert settings.main_context_limits.raw_tokens == 524288
    assert other.reasoning_effort == "high"
    assert other.command_timeout_seconds == 999
    assert other.execution_permissions == settings.execution_permissions
    assert other.for_model("a", "shared").api_key == "key-a"
    assert settings.model_choices() == (("aaa", "b"), ("shared", "a"), ("shared", "b"), ("z", "a"))
    assert "key-a" not in repr(settings)


def test_save_default_and_resume_are_independent(configured):
    path, settings = configured
    save_model_default(path, "b", "shared", "low")
    assert path.read_text().startswith("// keep credentials and comments")
    fresh = CorkiSettings.for_directory(path.parent, config_file=path)
    assert (fresh.provider_id, fresh.reasoning_effort, fresh.api_key) == ("b", "low", "key-b")
    for identity in ("a", "default"):
        old = CorkiSettings.for_directory(
            path.parent,
            config_file=path,
            resume_model_settings=ThreadModelSettings("shared", identity, "medium"),
        )
        assert old.api_key == "key-a"
        assert old.main_context_limits.raw_tokens == 524288
    assert (
        loads(path.read_text())["providers"]["a"]["models"]["shared"]["reasoning_effort"]
        == "medium"
    )


def test_failed_atomic_write_preserves_existing_default(configured, monkeypatch):
    from corki.config import toml_edits

    path, _ = configured
    before = path.read_bytes()

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(toml_edits, "write_text", fail)
    with pytest.raises(OSError):
        save_model_default(path, "b", "shared", "low")
    assert path.read_bytes() == before


def test_missing_provider_never_inherits_current_credentials(configured):
    path, settings = configured
    with pytest.raises(ValueError, match="unavailable"):
        settings.for_model("missing", "shared")
    assert settings.api_key == "key-a"


@pytest.mark.parametrize("default", [None, {"model": []}, {"provider": "a", "model": []}])
def test_invalid_defaults_fail_before_client_creation(configured, default):
    path, _ = configured
    document = loads(path.read_text())
    document["default"] = default
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        CorkiSettings.for_directory(path.parent, config_file=path)


def test_launcher_overrides_stay_with_initial_provider(configured, monkeypatch):
    path, _ = configured
    monkeypatch.setenv("CORKI_API_BASE", "http://127.0.0.1:19001/v1")
    monkeypatch.setenv("CORKI_API_KEY", "ephemeral-key")
    initial = CorkiSettings.for_directory(path.parent, config_file=path)
    other = initial.for_model("b", "shared")
    assert other.api_key == "key-b"
    back = other.for_model("a", "shared")
    assert back.api_key == "ephemeral-key"
    assert back.api_base == "http://127.0.0.1:19001/v1"


def test_alias_is_not_an_extra_menu_profile_and_saves_canonical_default(configured):
    path, _ = configured
    old = CorkiSettings.for_directory(
        path.parent,
        config_file=path,
        resume_model_settings=ThreadModelSettings("shared", "default", "medium"),
    )
    assert all(provider != "default" for _, provider in old.model_choices())
    save_model_default(path, "default", "shared", "low")
    saved = loads(path.read_text())
    assert saved["default"]["provider"] == "a"
    assert "provider" not in saved
    assert CorkiSettings.for_directory(path.parent, config_file=path).api_key == "key-a"


def test_alias_conflicts_fail_and_missing_default_identity_is_not_an_empty_profile(configured):
    path, _ = configured
    document = loads(path.read_text())
    document["providers"]["b"]["aliases"] = ["default"]
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="unique"):
        CorkiSettings.for_directory(path.parent, config_file=path)
    document["providers"]["b"].pop("aliases")
    document["providers"]["a"].pop("aliases")
    document["default"]["provider"] = "default"
    path.write_text(json.dumps(document))
    fallback = CorkiSettings.for_directory(path.parent, config_file=path)
    assert (fallback.provider_id, fallback.model) == ("a", "shared")


@pytest.mark.parametrize("trusted", [True, False])
def test_project_model_override_respects_trust_without_changing_provider(configured, trusted):
    path, _ = configured
    project = path.parent / "project"
    folder = project / ".corki"
    folder.mkdir(parents=True)
    (folder / "settings.json").write_text(
        json.dumps(
            {
                "agent": {"model": "z"},
                "default": {"provider": "b", "model": "aaa"},
            }
        )
    )
    document = loads(path.read_text())
    document["projects"] = {str(project): {"trust_level": "trusted" if trusted else "untrusted"}}
    path.write_text(json.dumps(document))
    settings = CorkiSettings.for_directory(project, config_file=path)
    assert settings.model == ("z" if trusted else "shared")
    assert settings.provider_id == "a"
    explicit = CorkiSettings.for_directory(project, config_file=path, model="shared")
    assert explicit.model == "shared"


def test_removed_profile_does_not_get_recreated_without_credentials(configured):
    path, settings = configured
    document = loads(path.read_text())
    document["providers"].pop("b")
    path.write_text(json.dumps(document))
    before = path.read_bytes()
    assert settings.for_model("b", "shared").api_key == "key-b"
    with pytest.raises(ValueError, match="no longer configured"):
        save_model_default(path, "b", "shared", "low")
    assert path.read_bytes() == before


def test_no_default_uses_first_provider_and_model_without_writing_file(tmp_path):
    path = tmp_path / "settings.json"
    document = {"providers": {
        "z-first": {"api_key": "first-fixture", "base_url": "https://first.example.invalid", "models": {"model-first": {}, "model-second": {}}},
        "a-second": {"api_key": "second-fixture", "models": {"other": {}}},
    }}
    path.write_text(json.dumps(document))
    original = path.read_text()
    settings = CorkiSettings.for_directory(tmp_path, config_file=path)
    assert (settings.provider_id, settings.model, settings.api_key) == ("z-first", "model-first", "first-fixture")
    assert path.read_text() == original
    selected = CorkiSettings.for_directory(tmp_path, config_file=path, provider="a-second")
    assert selected.provider_id == "a-second" and selected.model == "other"
    document["default"] = {"provider": "a-second", "model": "other"}
    path.write_text(json.dumps(document))
    assert CorkiSettings.for_directory(tmp_path, config_file=path).provider_id == "a-second"


def test_implicit_default_skips_closed_providers_and_all_closed_stays_blocked(tmp_path):
    path = tmp_path / "settings.json"
    document = {"providers": {
        "closed": {"enabled": False, "models": {"first": {}}},
        "available": {"models": {"second": {}}},
    }}
    path.write_text(json.dumps(document))
    assert CorkiSettings.for_directory(tmp_path, config_file=path).provider_id == "available"
    document["providers"]["available"]["enabled"] = False
    path.write_text(json.dumps(document))
    assert CorkiSettings.for_directory(tmp_path, config_file=path).provider_enabled is False


@pytest.mark.parametrize("default", [
    {}, {"provider": "closed", "model": "old"},
    {"provider": "removed", "model": "old"},
    {"provider": "z-first"}, {"provider": "z-first", "model": "removed"},
])
def test_unavailable_default_falls_back_without_rewriting_config(tmp_path, default):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"default": default, "providers": {
        "closed": {"enabled": False, "models": {"old": {}}},
        "z-first": {"models": {"first-model": {}, "second-model": {}}},
        "a-second": {"models": {"other-model": {}}},
    }}))
    original = path.read_bytes()
    settings = CorkiSettings.for_directory(tmp_path, config_file=path)
    assert (settings.provider_id, settings.model) == ("z-first", "first-model")
    assert path.read_bytes() == original
    explicit = CorkiSettings.for_directory(tmp_path, config_file=path, provider="closed")
    assert explicit.provider_id == "closed" and not explicit.provider_enabled


def test_each_provider_has_independent_default_and_fallback(configured):
    path, _ = configured
    data = loads(path.read_text())
    data["providers"]["a"]["default_model"] = "z"
    data["providers"]["b"]["default_model"] = "aaa"
    path.write_text(json.dumps(data))
    settings = CorkiSettings.for_directory(path.parent, config_file=path)
    assert (settings.provider_id, settings.model) == ("a", "z")
    assert settings.for_model("b", None).model == "aaa"
    assert settings.for_model("b", "shared").model == "shared"
    data["providers"]["a"]["default_model"] = "removed"
    data["providers"]["b"]["enabled"] = False
    data["default"]["provider"] = "b"
    path.write_text(json.dumps(data))
    selected = CorkiSettings.for_directory(path.parent, config_file=path)
    assert (selected.provider_id, selected.model) == ("a", "shared")
    assert loads(path.read_text()) == data


def test_saving_other_provider_preserves_legacy_provider_default(configured):
    path, _ = configured
    data = loads(path.read_text())
    data['default']['model'] = 'z'
    path.write_text(json.dumps(data))
    save_model_default(path, 'b', 'aaa', None)
    saved = loads(path.read_text())
    assert saved['providers']['a']['default_model'] == 'z'
    assert CorkiSettings.for_directory(path.parent, config_file=path).for_model('a', None).model == 'z'
