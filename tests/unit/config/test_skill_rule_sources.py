"""Native skill rules come from user/session authority, not project policy."""

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.config.skills import SkillRule, skill_rules_from_layers


def test_project_cannot_override_user_skill_rules(tmp_path):
    project = tmp_path / "project"
    (project / ".corki").mkdir(parents=True)
    (project / ".git").mkdir()
    (project / ".git/HEAD").write_text("ref: refs/heads/main\n")
    user = tmp_path / "config.toml"
    user.write_text(
        f'[projects."{project}"]\ntrust_level="trusted"\n'
        '[[skills.config]]\nname="private"\nenabled=false\n'
    )
    (project / ".corki/config.toml").write_text('[[skills.config]]\nname="private"\nenabled=true\n')
    settings = CorkiSettings.for_directory(project, config_file=user)
    assert any(
        layer.kind == "project" and layer.disabled_reason is None
        for layer in settings.configuration.layers
    )
    assert [(rule.name, rule.enabled) for rule in settings.skills_config] == [("private", False)]


def test_user_layers_keep_other_selectors_and_source_relative_paths(tmp_path):
    config = LocalConfigState(
        (
            ConfigLayer(
                tmp_path / "base/config.toml",
                "user",
                contents='[[skills.config]]\nname="same"\nenabled=false\n'
                '[[skills.config]]\npath="./private/SKILL.md"\nenabled=false\n'
                '[[skills.config]]\nname="other"\nenabled=false\n',
            ),
            ConfigLayer(
                tmp_path / "profile/config.toml",
                "user",
                contents='[[skills.config]]\nname="same"\nenabled=true\n',
            ),
            ConfigLayer(
                tmp_path / "blocked/config.toml",
                "user",
                disabled_reason="fixture",
                contents='[[skills.config]]\nname="other"\nenabled=true\n',
            ),
            ConfigLayer(
                tmp_path / "project/config.toml",
                "project",
                contents='[[skills.config]]\nname="other"\nenabled=true\n',
            ),
        )
    )
    rules = skill_rules_from_layers(config)
    assert rules == (
        SkillRule(False, path=tmp_path / "base/private/SKILL.md"),
        SkillRule(False, name="other"),
        SkillRule(True, name="same"),
    )
    assert all(rule.source == "layers" for rule in rules)


def test_loaded_rule_provenance_is_not_guessed_from_equal_host_value(tmp_path):
    user = tmp_path / "config.toml"
    user.write_text('[[skills.config]]\nname="same"\nenabled=false\n')
    (rule,) = CorkiSettings.for_directory(tmp_path, config_file=user).skills_config
    host = SkillRule(False, name="same")
    assert rule == host
    assert rule.source == "layers" and host.source == "host"
    with pytest.raises(ValueError, match="source"):
        SkillRule(False, name="same", source="project")


@pytest.mark.parametrize(
    "invalid",
    [
        'include_instructions="no"',
        "max_context_tokens=0",
        "max_context_tokens=true",
        "bundled=false",
        'bundled={enabled="false"}',
        'config="not an array"',
    ],
)
def test_invalid_skills_layer_does_not_displace_valid_lower_rules(tmp_path, invalid, caplog):
    config = LocalConfigState(
        (
            ConfigLayer(
                tmp_path / "base.toml",
                "user",
                contents='[[skills.config]]\nname="private"\nenabled=false\n',
            ),
            ConfigLayer(
                tmp_path / "profile.toml",
                "user",
                contents="[skills]\n"
                + invalid
                + "\n"
                + (
                    '[[skills.config]]\nname="private"\nenabled=true\n'
                    if not invalid.startswith("config=")
                    else ""
                ),
            ),
        )
    )
    assert skill_rules_from_layers(config) == (SkillRule(False, name="private"),)
    assert "ignoring invalid skills" in caplog.text
