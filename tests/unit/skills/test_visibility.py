import asyncio
import json

import pytest

from corki.config import CorkiSettings
from corki.config.skills import SkillRule, disabled_skill_paths, parse_skill_rules
from corki.protocol.ids import ToolCallId
from corki.protocol.tools import ToolCall
from corki.skills import SkillListTool, SkillService
from corki.tools import ToolContext


def write_skill(root, name, policy=None):
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: fixture\n---\nBODY {name}", encoding="utf-8")
    sidecar = path.parent / "agents" / "openai.yaml"
    if policy is not None:
        sidecar.parent.mkdir(exist_ok=True)
        sidecar.write_text(policy, encoding="utf-8")
    return path, sidecar


def test_bundled_hidden_skill_is_not_in_catalog_but_can_be_explicitly_selected(tmp_path):
    service = SkillService(home=tmp_path, project_root=tmp_path)
    assert not any(
        line.startswith("- review-agent:") for line in service.render_catalog(tmp_path).splitlines()
    )
    selected = service.explicit_mentions("Use $review-agent", tmp_path)
    assert len(selected) == 1
    assert "Review Agent" in service.read(selected[0])


def test_model_list_does_not_redisclose_an_implicitly_hidden_skill(tmp_path):
    write_skill(tmp_path / "skills", "hidden", "policy:\n  allow_implicit_invocation: false\n")
    service = SkillService(home=tmp_path, project_root=tmp_path, bundled_enabled=False)
    call = ToolCall(ToolCallId("list"), "skill_list", {})
    result = asyncio.run(SkillListTool(service).execute(call, ToolContext(cwd=tmp_path)))
    assert json.loads(result.content)["skills"] == []
    assert len(service.explicit_mentions("$hidden", tmp_path)) == 1


@pytest.mark.parametrize(
    "contents",
    [
        "policy: [bad]",
        "policy:\n  allow_implicit_invocation: 'false'",
        "interface: [bad]\npolicy:\n  allow_implicit_invocation: false",
        "dependencies:\n  tools: null\npolicy:\n  allow_implicit_invocation: false",
        "policy:\n  allow_implicit_invocation: false\n  products: [unknown]",
        "[invalid yaml",
        "policy:\n  allow_implicit_invocation: null",
    ],
)
def test_bad_optional_metadata_keeps_valid_skill_with_default_policy(tmp_path, contents):
    write_skill(tmp_path / "skills", "fixture", contents)
    service = SkillService(home=tmp_path, project_root=tmp_path, bundled_enabled=False)
    snapshot = service.snapshot(tmp_path)
    assert len(snapshot.skills) == 1 and snapshot.errors == ()
    assert snapshot.skills[0].allow_implicit_invocation
    assert "- fixture:" in service.render_catalog(tmp_path)


def test_sidecar_create_edit_and_delete_invalidate_cached_policy(tmp_path):
    _, sidecar = write_skill(tmp_path / "skills", "fixture")
    service = SkillService(home=tmp_path, project_root=tmp_path, bundled_enabled=False)
    first = service.snapshot(tmp_path)
    sidecar.parent.mkdir()
    sidecar.write_text("policy:\n  allow_implicit_invocation: false\n", encoding="utf-8")
    hidden = service.snapshot(tmp_path)
    assert hidden is not first and not hidden.skills[0].allow_implicit_invocation
    assert service.render_catalog(tmp_path) == ""
    assert service.snapshot(tmp_path) is hidden
    sidecar.write_text("policy:\n  allow_implicit_invocation: true\n", encoding="utf-8")
    assert service.snapshot(tmp_path).skills[0].allow_implicit_invocation
    sidecar.unlink()
    assert service.snapshot(tmp_path).skills[0].allow_implicit_invocation


def test_disabled_path_does_not_remove_enabled_same_name_in_another_root(tmp_path):
    project = tmp_path / "project"
    home = tmp_path / "home"
    winner, _ = write_skill(project / ".corki/skills", "same")
    fallback, _ = write_skill(home / "skills", "same")
    service = SkillService(
        home=home,
        project_root=project,
        bundled_enabled=False,
        rules=(SkillRule(False, path=winner),),
    )
    snapshot = service.snapshot(project)
    assert [skill.path for skill in snapshot.skills] == [winner.resolve(), fallback.resolve()]
    assert [skill.path for skill in service.explicit_mentions("$same", project)] == [
        fallback.resolve()
    ]
    assert snapshot.resolve("same").path == fallback.resolve()
    assert str(home / "skills") in service.render_catalog(project)
    assert str(project / ".corki/skills") not in service.render_catalog(project)


@pytest.mark.parametrize("last_enabled", [False, True])
def test_later_name_or_path_rule_wins_and_unloaded_path_remains_disabled(tmp_path, last_enabled):
    path = tmp_path / "fixture/SKILL.md"
    absent = tmp_path / "future/SKILL.md"
    rules = (
        SkillRule(True, name="fixture"),
        SkillRule(False, path=path),
        SkillRule(last_enabled, name="fixture"),
        SkillRule(False, path=absent),
    )
    disabled = disabled_skill_paths(rules, (("fixture", path),))
    assert (path in disabled) is not last_enabled
    assert absent in disabled


def test_toml_rule_selectors_canonicalize_and_last_occurrence_is_ordered(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        '[[skills.config]]\nname="fixture"\nenabled=false\n'
        '[[skills.config]]\npath="fixture/SKILL.md"\nenabled=false\n'
        '[[skills.config]]\nname="fixture"\nenabled=true\n',
        encoding="utf-8",
    )
    settings = CorkiSettings.for_directory(tmp_path, config_file=config)
    assert settings.skills_config == (
        SkillRule(False, path=tmp_path / "fixture/SKILL.md"),
        SkillRule(True, name="fixture"),
    )


def test_invalid_rule_selectors_are_logged_and_do_not_disable_arbitrary_skills(tmp_path, caplog):
    rules = parse_skill_rules(
        [
            {"name": "", "enabled": False},
            {"enabled": False},
            {"path": "x", "name": "fixture", "enabled": False},
        ],
        tmp_path,
    )
    assert rules == () and "ignoring skills.config" in caplog.text
    assert parse_skill_rules([{"name": "fixture", "enabled": "false"}], tmp_path) == ()
