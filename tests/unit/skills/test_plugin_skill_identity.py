"""Native source identity is not the human-facing skill name or namespace."""

import os
from dataclasses import replace

import pytest

from corki.protocol.input_mentions import InputMention
from corki.skills.discovery import SkillRoot, discover_skills
from corki.skills.models import SkillDiscoveryMode, SkillScope
from corki.skills.service import PluginSkillRoot, SkillService


def skill_file(root, folder, name="guide"):
    path = root / folder / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: fixture\n---\n{folder}")
    return path


def test_migrated_command_is_shadowed_only_by_its_own_plugins_authored_skill(tmp_path):
    roots = []
    for market in ("first", "second"):
        plugin = tmp_path / market
        migrated = plugin / ".codex-plugin/migrated-command-skills"
        skill_file(migrated, "guide")
        roots.append(SkillRoot(migrated, SkillScope.PLUGIN, "fixture", f"fixture@{market}", plugin))
    native = tmp_path / "first/skills"
    authored = skill_file(native, "guide")
    roots.append(
        SkillRoot(native, SkillScope.PLUGIN, "fixture", "fixture@first", tmp_path / "first")
    )
    snapshot = discover_skills(tuple(roots))
    assert [(s.plugin_id, s.path) for s in snapshot.skills] == [
        ("fixture@first", authored),
        (
            "fixture@second",
            tmp_path / "second/.codex-plugin/migrated-command-skills/guide/SKILL.md",
        ),
    ]


@pytest.mark.parametrize("locator", ["first", "second", "missing"])
def test_host_path_selection_does_not_fall_back_to_an_ambiguous_plain_name(tmp_path, locator):
    home = tmp_path / "home"
    skill_file(home / "skills", "first")
    second = skill_file(home / "skills", "second")
    service = SkillService(home=home, project_root=tmp_path, bundled_enabled=False)
    path = home / "skills" / locator / "SKILL.md"
    structured = service.explicit_mentions(
        "$guide", tmp_path, mentions=(InputMention("guide", str(path), "skill"),)
    )
    assert [s.path for s in structured] == ([] if locator == "missing" else [path])
    textual = service.explicit_mentions(f"[$guide]({second}) $guide", tmp_path)
    assert [s.path for s in textual] == [second]
    assert len(service.snapshot(tmp_path).skills) == 2


def test_agent_format_changes_root_cache_and_preserves_legacy_symlinks(tmp_path):
    root = tmp_path / "plugin"
    direct = skill_file(root / "skills", "direct", "direct")
    nested = skill_file(root / "skills", "group/nested", "nested")
    outside = skill_file(tmp_path / "outside", "linked", "linked")
    (root / "skills/linked").symlink_to(outside.parent, target_is_directory=True)
    original = PluginSkillRoot("fixture", root / "skills", "fixture@lab", root)
    service = SkillService(
        home=tmp_path / "home",
        project_root=tmp_path,
        bundled_enabled=False,
        plugin_roots=(original,),
    )
    before = service.snapshot(tmp_path)
    assert {s.path for s in before.skills} == {direct, nested, outside}
    updated = service.with_plugin_roots(
        (replace(original, discovery_mode=SkillDiscoveryMode.DIRECT_CHILDREN),)
    )
    after = updated.snapshot(tmp_path)
    assert [s.path for s in after.skills] == [direct]
    assert updated.snapshot(tmp_path) is after
    assert service.snapshot(tmp_path) is before


def test_distinct_roots_for_same_canonical_path_keep_first_source(tmp_path):
    path = skill_file(tmp_path / "original", "guide")
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path / "original", target_is_directory=True)
    snapshot = discover_skills(
        (
            SkillRoot(tmp_path / "original", SkillScope.USER),
            SkillRoot(alias, SkillScope.PLUGIN, "plugin", "plugin@lab", tmp_path),
        )
    )
    assert len(snapshot.skills) == 1
    assert snapshot.skills[0].path == path
    assert snapshot.skills[0].plugin_id is None


@pytest.mark.parametrize("folder", ["references", "scripts", "assets", "templates"])
def test_agent_direct_child_name_is_not_a_support_directory_exclusion(tmp_path, folder):
    path = skill_file(tmp_path / "skills", folder)
    snapshot = discover_skills(
        (
            SkillRoot(
                tmp_path / "skills",
                SkillScope.PLUGIN,
                "fixture",
                "fixture@lab",
                tmp_path,
                SkillDiscoveryMode.DIRECT_CHILDREN,
            ),
        )
    )
    assert [s.path for s in snapshot.skills] == [path]


def test_symlink_target_change_invalidates_equal_stat_skill_cache(tmp_path):
    plugin = tmp_path / "plugin"
    internal = skill_file(plugin / "private", "guide")
    outside = skill_file(tmp_path / "outside", "guide")
    state = internal.stat()
    os.utime(outside, ns=(state.st_atime_ns, state.st_mtime_ns))
    assert outside.stat().st_size == state.st_size
    (plugin / "skills").mkdir()
    link = plugin / "skills/guide"
    link.symlink_to(internal.parent, target_is_directory=True)
    service = SkillService(
        home=tmp_path / "home",
        project_root=tmp_path,
        bundled_enabled=False,
        plugin_roots=(
            PluginSkillRoot(
                "fixture",
                plugin / "skills",
                "fixture@lab",
                plugin,
                SkillDiscoveryMode.DIRECT_CHILDREN,
            ),
        ),
    )
    before = service.snapshot(tmp_path)
    assert [s.path for s in before.skills] == [internal]
    link.unlink()
    link.symlink_to(outside.parent, target_is_directory=True)
    after = service.snapshot(tmp_path)
    assert after is not before and after.skills == ()
