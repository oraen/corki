import pytest

from corki.skills import SkillService
from corki.skills.mentions import extract


def service_with_skills(tmp_path):
    paths = {}
    for name in ("alpha", "beta", "PATH"):
        path = tmp_path / "skills" / name / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(f"---\nname: {name}\ndescription: fixture\n---\n", encoding="utf-8")
        paths[name] = path
    return SkillService(home=tmp_path, project_root=tmp_path, bundled_enabled=False), paths


def test_linked_path_controls_selection_instead_of_its_label(tmp_path):
    service, paths = service_with_skills(tmp_path)
    selected = service.explicit_mentions(f"[$alpha]({paths['beta']})", tmp_path)
    assert [skill.name for skill in selected] == ["beta"]
    assert service.explicit_mentions("[$alpha](app://alpha)", tmp_path) == ()
    assert service.explicit_mentions("[$alpha](/unknown/SKILL.md)", tmp_path) == ()


def test_environment_variables_case_and_dot_boundaries_follow_host_selection(tmp_path):
    service, _ = service_with_skills(tmp_path)
    assert service.explicit_mentions("$PATH and $ALPHA", tmp_path) == ()
    assert [s.name for s in service.explicit_mentions("$alpha.skill", tmp_path)] == ["alpha"]


def test_plain_mentions_keep_discovery_order_not_user_text_order(tmp_path):
    service, _ = service_with_skills(tmp_path)
    assert [s.name for s in service.explicit_mentions("$beta then $alpha", tmp_path)] == [
        "alpha",
        "beta",
    ]


@pytest.mark.parametrize(
    "text,names,paths",
    [
        ("use $alpha and [$beta](/tmp/beta)", {"alpha"}, {"/tmp/beta"}),
        ("[$HOME](/tmp/skill) $XDG_CONFIG_HOME $beta", {"beta"}, set()),
        ("[beta](/tmp/beta)", set(), set()),
        ("[$beta] /tmp/beta", {"beta"}, set()),
        ("[$beta]()", {"beta"}, set()),
        ("[$beta]   ( /tmp/beta )", set(), {"/tmp/beta"}),
        ("$alpha.skill $beta_extra $slack:search", {"alpha", "beta_extra", "slack:search"}, set()),
        ("$" * 256, set(), set()),
        ("word$alpha", {"alpha"}, set()),
    ],
)
def test_codex_text_mention_boundaries(text, names, paths):
    assert extract(text) == (names, paths)
