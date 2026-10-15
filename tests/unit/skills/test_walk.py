"""Native filesystem walk limits count inventory, not only matching skill files."""

import asyncio

import pytest

from corki.skills.discovery import SkillRoot, discover_skills
from corki.skills.models import SkillDiscoveryMode, SkillScope
from corki.skills.service import SkillService
from corki.skills.walk import walk_skill_files


def write_skill(root, folder):
    path = root / folder / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: guide\ndescription: {folder}\n---\nBODY")
    return path


@pytest.mark.parametrize("mode", list(SkillDiscoveryMode))
def test_regular_skill_files_only_and_directory_cycles_terminate(tmp_path, mode):
    actual = write_skill(tmp_path, "actual")
    (tmp_path / "file-link").mkdir()
    (tmp_path / "file-link/SKILL.md").symlink_to(actual)
    (tmp_path / "missing").symlink_to(tmp_path / "absent")
    (tmp_path / "cycle").symlink_to(tmp_path, target_is_directory=True)
    outcome = walk_skill_files(tmp_path, mode=mode)
    assert outcome.files == (actual,)
    assert not outcome.errors and not outcome.truncated


def test_hidden_directory_alias_is_visible_but_not_hidden_path(tmp_path):
    write_skill(tmp_path, ".hidden/guide")
    alias = tmp_path / "visible"
    alias.symlink_to(tmp_path / ".hidden", target_is_directory=True)
    assert walk_skill_files(tmp_path).files == (alias / "guide/SKILL.md",)


def test_root_depth_zero_through_six_are_scanned_but_not_seven(tmp_path):
    paths = [write_skill(tmp_path, "/".join(["child"] * depth)) for depth in range(8)]
    outcome = walk_skill_files(tmp_path)
    assert outcome.files == tuple(paths[:7])
    assert not outcome.truncated  # Depth is a scope, not a truncated inventory.


def test_directory_limit_keeps_root_files_and_already_queued_siblings(tmp_path, monkeypatch):
    root_file = write_skill(tmp_path, "")
    first = write_skill(tmp_path, "a")
    write_skill(tmp_path, "z")
    monkeypatch.setattr("corki.skills.walk.MAX_DIRECTORIES", 2)
    outcome = walk_skill_files(tmp_path)
    assert outcome.files == (root_file, first)
    assert outcome.truncated


def test_entry_limit_counts_non_skills_before_breadth_first_descent(tmp_path, monkeypatch):
    write_skill(tmp_path, "a/nested")
    sibling = write_skill(tmp_path, "z")
    # root: a,z; a: nested; z: SKILL.md; a/nested: SKILL.md (over limit).
    monkeypatch.setattr("corki.skills.walk.MAX_ENTRIES", 4)
    outcome = walk_skill_files(tmp_path)
    assert outcome.files == (sibling,)
    assert outcome.truncated


def test_response_limit_counts_uri_bytes_and_item_overhead(tmp_path, monkeypatch):
    path = write_skill(tmp_path, "")
    cost = len(path.as_uri().encode()) + 64
    monkeypatch.setattr("corki.skills.walk.MAX_RESPONSE_BYTES", cost)
    assert walk_skill_files(tmp_path).files == (path,)
    monkeypatch.setattr("corki.skills.walk.MAX_RESPONSE_BYTES", cost - 1)
    outcome = walk_skill_files(tmp_path)
    assert outcome.files == () and outcome.truncated


def test_scan_error_preserves_sibling_and_does_not_become_parse_error(
    tmp_path, monkeypatch, caplog
):
    good = write_skill(tmp_path, "z")
    write_skill(tmp_path, "a")
    import corki.skills.walk as walk

    original = walk.os.scandir

    def scan(path):
        if path == tmp_path / "a":
            raise PermissionError("fixture denied")
        return original(path)

    monkeypatch.setattr(walk.os, "scandir", scan)
    snapshot = discover_skills((SkillRoot(tmp_path, SkillScope.USER),))
    assert [skill.path for skill in snapshot.skills] == [good]
    assert snapshot.errors == ()
    assert "fixture denied" in caplog.text


def test_cancel_control_flow_is_not_turned_into_scan_warning(tmp_path, monkeypatch):
    def cancel(path):
        raise asyncio.CancelledError

    monkeypatch.setattr("corki.skills.walk.os.scandir", cancel)
    with pytest.raises(asyncio.CancelledError):
        walk_skill_files(tmp_path)


def test_service_reuses_one_inventory_for_signature_and_parse(tmp_path, monkeypatch):
    write_skill(tmp_path / "home/skills", "guide")
    import corki.skills.service as service_module

    original = service_module.scan_skill_root
    scanned = []

    def scan(root):
        scanned.append(root)
        return original(root)

    monkeypatch.setattr(service_module, "scan_skill_root", scan)
    service = SkillService(home=tmp_path / "home", project_root=tmp_path, bundled_enabled=False)
    first = service.snapshot(tmp_path)
    assert len(scanned) == len(set(scanned))
    assert len(first.skills) == 1
    scanned.clear()
    assert service.snapshot(tmp_path) is first
    assert len(scanned) == len(set(scanned))


def test_truncation_recovery_replaces_cached_empty_inventory(tmp_path, monkeypatch, caplog):
    home = tmp_path / "home"
    path = write_skill(home / "skills", "guide")
    service = SkillService(home=home, project_root=tmp_path, bundled_enabled=False)
    monkeypatch.setattr("corki.skills.walk.MAX_DIRECTORIES", 1)
    first = service.snapshot(tmp_path)
    assert first.skills == ()
    assert "traversal limit" in caplog.text
    monkeypatch.setattr("corki.skills.walk.MAX_DIRECTORIES", 2)
    restored = service.snapshot(tmp_path)
    assert restored is not first
    assert [s.path for s in restored.skills] == [path]


def test_system_root_directory_link_is_not_followed(tmp_path):
    write_skill(tmp_path / "target", "guide")
    root = tmp_path / "system-link"
    root.symlink_to(tmp_path / "target", target_is_directory=True)
    assert walk_skill_files(root, follow_directory_links=False).files == ()


@pytest.mark.parametrize(
    "folder, spelling", [("bang!", "bang!"), ("中#", "%E4%B8%AD%23"), ("a;@=", "a;@=")]
)
def test_response_budget_uses_rust_file_uri_punctuation(tmp_path, monkeypatch, folder, spelling):
    root = tmp_path / folder
    path = write_skill(root, "")
    cost = len(tmp_path.as_uri() + "/" + spelling + "/SKILL.md") + 64
    monkeypatch.setattr("corki.skills.walk.MAX_RESPONSE_BYTES", cost)
    assert walk_skill_files(root).files == (path,)
    monkeypatch.setattr("corki.skills.walk.MAX_RESPONSE_BYTES", cost - 1)
    assert walk_skill_files(root).truncated
