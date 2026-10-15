"""Map Codex extension pruning scope, cutoff, and best-effort failure contracts."""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from corki.memory.extension_retention import prune_old_extension_resources

NOW = datetime(2026, 4, 14, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("name", "removed"),
    [
        ("2026-04-06T11-59-59-old.md", True),
        ("2026-04-07T12-00-00-cutoff.md", True),
        ("2026-04-07T12-00-01-recent.md", False),
        ("2026-04-07T11-59-60-leap.md", True),
        ("2026-04-07T12-00-60-leap.md", False),
        ("2026-04-07T12-00-00.md", True),
        ("2026-04-07T12-00-00suffix.md", True),
        ("2026-04-07T12-00-00-中文.md", True),
        ("2026-04-06T11-59-59.txt", False),
        ("2026-04-06T11-59-59.MD", False),
        ("2026-02-30T12-00-00-invalid.md", False),
        ("2026-04-06T24-00-00-invalid.md", False),
        ("not-a-timestamp.md", False),
        ("2026.md", False),
    ],
)
def test_filename_timestamp_not_mtime_controls_expiry(tmp_path, name, removed):
    extension = tmp_path / "extensions/team"
    resources = extension / "resources"
    resources.mkdir(parents=True)
    instructions = extension / "instructions.md"
    instructions.write_text("keep instructions")
    resource = resources / name
    resource.write_text("resource")
    stamp = NOW.timestamp() + 86400 if removed else 0
    os.utime(resource, (stamp, stamp))
    assert prune_old_extension_resources(tmp_path, now=NOW) is removed
    assert resource.exists() is not removed
    assert instructions.read_text() == "keep instructions"


def test_scope_is_direct_resources_of_extensions_with_instructions(tmp_path):
    paths = [
        "extensions/team/resources/nested/2026-04-06T11-59-59-old.md",
        "extensions/ignored/resources/2026-04-06T11-59-59-old.md",
        "extensions/team/2026-04-06T11-59-59-old.md",
        "extensions/ad_hoc/notes/2026-04-06T11-59-59-old.md",
        "skills/2026-04-06T11-59-59-old.md",
        "MEMORY.md",
    ]
    for name in paths:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("preserve")
    (tmp_path / "extensions/team/instructions.md").write_text("keep")
    (tmp_path / "extensions/ad_hoc/instructions.md").write_text("keep")
    directory = tmp_path / "extensions/team/resources/2026-04-06T11-59-59-directory.md"
    directory.mkdir()
    assert not prune_old_extension_resources(tmp_path, now=NOW)
    assert all((tmp_path / name).read_text() == "preserve" for name in paths)
    assert directory.is_dir()


@pytest.mark.parametrize("link_kind", ["extension", "resources", "file", "root"])
def test_pruning_does_not_follow_symlinks(tmp_path, link_kind):
    root = tmp_path / "memories"
    extension = root / "extensions/team"
    resources = extension / "resources"
    resources.mkdir(parents=True)
    (extension / "instructions.md").write_text("keep")
    target = tmp_path / "outside"
    target.mkdir()
    outside = target / "2026-04-06T11-59-59-old.md"
    outside.write_text("outside")
    if link_kind == "extension":
        (root / "extensions/linked").symlink_to(target, target_is_directory=True)
        (target / "instructions.md").write_text("keep")
        (target / "resources").mkdir()
        outside.rename(target / "resources" / outside.name)
        outside = target / "resources" / outside.name
    elif link_kind == "resources":
        resources.rmdir()
        resources.symlink_to(target, target_is_directory=True)
    elif link_kind == "root":
        linked = tmp_path / "linked"
        linked.symlink_to(root, target_is_directory=True)
        root = linked
    else:
        (resources / outside.name).symlink_to(outside)
    assert not prune_old_extension_resources(root, now=NOW)
    assert outside.read_text() == "outside"


@pytest.mark.parametrize("failure", ["missing", "denied", "list"])
def test_io_failure_does_not_abort_other_extensions(tmp_path, monkeypatch, caplog, failure):
    files = []
    for name in ("one", "two"):
        resources = tmp_path / f"extensions/{name}/resources"
        resources.mkdir(parents=True)
        (resources.parent / "instructions.md").write_text("keep")
        resource = resources / "2026-04-06T11-59-59-old.md"
        resource.write_text("old")
        files.append(resource)
    unlink, iterdir = Path.unlink, Path.iterdir

    def remove(path, *args, **kwargs):
        if path == files[0]:
            if failure == "missing":
                unlink(path)
                raise FileNotFoundError("concurrently removed")
            if failure == "denied":
                raise PermissionError("denied resource")
        return unlink(path, *args, **kwargs)

    def entries(path):
        if path == files[0].parent and failure == "list":
            raise PermissionError("denied listing")
        return iterdir(path)

    monkeypatch.setattr(Path, "unlink", remove)
    monkeypatch.setattr(Path, "iterdir", entries)
    assert prune_old_extension_resources(tmp_path, now=NOW)
    assert not files[1].exists()
    assert files[0].exists() is (failure != "missing")
    assert ("denied" in caplog.text) is (failure != "missing")


def test_missing_extensions_is_a_quiet_noop(tmp_path, caplog):
    assert not prune_old_extension_resources(tmp_path, now=NOW)
    assert not caplog.records
