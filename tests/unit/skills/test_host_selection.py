"""Native host selector ordering, disambiguation, and typed-input boundaries."""

from dataclasses import replace

from corki.protocol import InputMention
from corki.skills.mentions import select
from corki.skills.models import SkillMetadata, SkillScope, SkillSnapshot


def skill(tmp_path, name, folder):
    return SkillMetadata(name, "fixture", tmp_path / folder / "SKILL.md", tmp_path, SkillScope.USER)


def test_text_paths_follow_catalog_order_but_typed_paths_are_selected_first(tmp_path):
    alpha, beta = skill(tmp_path, "alpha", "z"), skill(tmp_path, "beta", "a")
    snapshot = SkillSnapshot((alpha, beta))
    text = f"[$beta]({beta.path}) [$alpha]({alpha.path})"
    assert select(text, snapshot) == (alpha, beta)
    assert select(text, snapshot, mentions=(InputMention("beta", str(beta.path), "skill"),)) == (
        beta,
        alpha,
    )


def test_disabled_duplicate_does_not_make_enabled_plain_name_ambiguous(tmp_path):
    first, second = skill(tmp_path, "guide", "first"), skill(tmp_path, "guide", "second")
    snapshot = SkillSnapshot((first, second))
    assert select("$guide", snapshot) == ()
    assert select("$guide", replace(snapshot, disabled_paths=frozenset((first.path,)))) == (second,)
    assert (
        select(
            f"[$guide]({first.path})", replace(snapshot, disabled_paths=frozenset((first.path,)))
        )
        == ()
    )


def test_generic_mention_is_not_a_typed_host_skill_selection(tmp_path):
    entry = skill(tmp_path, "guide", "guide")
    snapshot = SkillSnapshot((entry,))
    mention = InputMention("guide", str(entry.path))
    assert select("selected", snapshot, mentions=(mention,)) == ()
    assert select("$guide", snapshot, mentions=(mention,)) == (entry,)
    assert select("selected", snapshot, mentions=(replace(mention, kind="skill"),)) == (entry,)


def test_missing_typed_path_blocks_only_its_plain_label(tmp_path):
    alpha, beta = skill(tmp_path, "alpha", "alpha"), skill(tmp_path, "beta", "beta")
    mention = InputMention("alpha", str(tmp_path / "missing/SKILL.md"), "skill")
    assert select("$alpha $beta", SkillSnapshot((alpha, beta)), mentions=(mention,)) == (beta,)
