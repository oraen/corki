"""Native NTFS junctions must obey the same traversal policy as directory links."""

import os
import subprocess

import pytest

from corki.plugins.manifest_path import find_manifest
from corki.skills.walk import walk_skill_files

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires real NTFS junctions")


def junction(link, target):
    # Both paths are generated fixtures, not model/user command arguments.
    assert not any('"' in str(p) for p in (link, target))
    subprocess.run(
        f'cmd.exe /d /c mklink /J "{link}" "{target}"',
        check=True, capture_output=True, timeout=30,
    )


@pytest.mark.parametrize("root_alias", [False, True])
def test_junction_respects_follow_policy(tmp_path, root_alias):
    root = tmp_path / "skills"
    root.mkdir()
    target = tmp_path / "外部 技能"
    target.mkdir()
    (target / "SKILL.md").write_text("fixture", encoding="utf-8")
    link = root / "linked"
    junction(link, target)
    try:
        selected = link if root_alias else root
        assert walk_skill_files(selected, follow_directory_links=False).files == ()
        followed = walk_skill_files(selected, follow_directory_links=True)
        assert followed.files == (link / "SKILL.md",)
        assert not followed.errors and not followed.truncated
    finally:
        link.rmdir()


def test_junction_cycles_and_missing_targets(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    skill = root / "SKILL.md"
    skill.write_text("fixture", encoding="utf-8")
    target = tmp_path / "removed"
    target.mkdir()
    cycle, missing = root / "cycle", root / "missing"
    junction(cycle, root)
    junction(missing, target)
    target.rmdir()
    try:
        result = walk_skill_files(root)
        assert result.files == (skill,)
        assert not result.errors and not result.truncated
    finally:
        cycle.rmdir()
        missing.rmdir()


@pytest.mark.parametrize(
    "folder", [".corki-plugin", ".codex-plugin", ".claude-plugin", ".cursor-plugin"]
)
def test_plugin_metadata_directory_rejects_junction(tmp_path, folder):
    root = tmp_path / "package"
    root.mkdir()
    target = tmp_path / "external metadata"
    target.mkdir()
    filename = "plugin.toml" if folder == ".corki-plugin" else "plugin.json"
    (target / filename).write_text("{}", encoding="utf-8")
    link = root / folder
    junction(link, target)
    try:
        assert find_manifest(root) is None
    finally:
        link.rmdir()
