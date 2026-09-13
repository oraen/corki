"""A reloaded service owns its rules/cache; admitted services remain unchanged."""

from corki.config.layers import ConfigLayer, LocalConfigState
from corki.config.skills import SkillRule
from corki.skills.service import SkillService


def test_disable_reenable_and_remove_rules_without_mutating_old_views(tmp_path):
    home = tmp_path / "home"
    skill = home / "skills/example/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: example\ndescription: fixture\n---\nfixture body\n")
    service = SkillService(home=home, project_root=tmp_path, bundled_enabled=False)

    def state(contents):
        return LocalConfigState((ConfigLayer(home / "config.toml", "user", contents=contents),))

    assert service.snapshot(tmp_path).resolve("example") is not None
    disabled = service.with_configuration(
        state('[[skills.config]]\nname="example"\nenabled=false\n')
    )
    assert disabled.snapshot(tmp_path).resolve("example") is None
    enabled = disabled.with_configuration(
        state('[[skills.config]]\nname="example"\nenabled=true\n')
    )
    removed = disabled.with_configuration(state(""))
    for view in (service, enabled, removed):
        assert view.snapshot(tmp_path).resolve("example") is not None
    assert disabled.snapshot(tmp_path).resolve("example") is None
    assert service._lock is not disabled._lock
    assert disabled._lock is not enabled._lock


def test_host_rule_survives_file_rule_replacement(tmp_path):
    service = SkillService(
        home=tmp_path,
        project_root=tmp_path,
        bundled_enabled=False,
        rules=(SkillRule(False, name="private"),),
    )
    state = LocalConfigState(
        (
            ConfigLayer(
                tmp_path / "config.toml",
                "user",
                contents='[[skills.config]]\nname="private"\nenabled=true\n'
                '[[skills.config]]\nname="other"\nenabled=false\n',
            ),
        )
    )
    reloaded = service.with_configuration(state)
    assert [(rule.name, rule.enabled, rule.source) for rule in reloaded._rules] == [
        ("private", True, "layers"),
        ("other", False, "layers"),
        ("private", False, "host"),
    ]
    # Neither unrelated file rules nor host authority are frozen from an old reload.
    cleared = reloaded.with_configuration(LocalConfigState())
    assert cleared._rules == (SkillRule(False, name="private"),)
    assert cleared._rules[0].source == "host"
