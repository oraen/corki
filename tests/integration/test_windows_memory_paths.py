"""Memory boundaries on native Windows; real junctions, no symlink privilege required."""

import asyncio
import os
import subprocess

import pytest

from corki.memory.artifacts import ensure_memory_layout, remove_memory_symlinks
from corki.memory.backend import LocalMemoryBackend, MemoryPathError
from corki.memory.workspace import workspace_files

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires NTFS junctions")


def junction(link, target):
    assert not any('"' in str(p) for p in (link, target))
    subprocess.run(
        f'cmd.exe /d /c mklink /J "{link}" "{target}"',
        check=True,
        capture_output=True,
        timeout=30,
    )


def test_memory_junction_not_read_searched_or_traversed_during_cleanup(tmp_path):
    root, outside = tmp_path / "memory", tmp_path / "外部 目录"
    backend = LocalMemoryBackend(root)
    outside.mkdir()
    target = outside / "keep.txt"
    target.write_text("unique-outside-fixture", encoding="utf-8")
    link = root / "linked"
    junction(link, outside)
    try:
        assert "linked" not in {entry["path"] for entry in backend.list()["entries"]}
        with pytest.raises(MemoryPathError):
            asyncio.run(backend.read("linked/keep.txt"))
        assert not asyncio.run(backend.search(("unique-outside-fixture",)))["matches"]
        with pytest.raises(ValueError, match="symbolic link"):
            workspace_files(root)
        assert remove_memory_symlinks(root) == 1
        assert not link.exists()
        assert target.read_text(encoding="utf-8") == "unique-outside-fixture"
    finally:
        if link.exists():
            link.rmdir()


def test_memory_root_junction_rejected_before_layout_writes(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "memory"
    junction(link, outside)
    try:
        with pytest.raises(ValueError, match="symbolic link"):
            ensure_memory_layout(link)
        assert list(outside.iterdir()) == []
    finally:
        link.rmdir()


@pytest.mark.parametrize(
    "path",
    [
        r"..\outside\file.txt",
        r"C:\outside\file.txt",
        "C:relative",
        r"\outside\file.txt",
        "file.txt:stream",
    ],
)
def test_memory_windows_path_cannot_escape_portable_namespace(tmp_path, path):
    backend = LocalMemoryBackend(tmp_path / "memory")
    with pytest.raises(MemoryPathError):
        backend.list(path)


@pytest.mark.parametrize("component", ["root", "extensions", "extension", "resources"])
def test_expiry_does_not_remove_resources_through_junctions(tmp_path, component):
    from datetime import UTC, datetime

    from corki.memory.extension_retention import prune_old_extension_resources

    root = tmp_path / "memory"
    resources = root / "extensions/demo/resources"
    resources.mkdir(parents=True)
    (resources.parent / "instructions.md").write_text("fixture", encoding="utf-8")
    retained = resources / "2020-01-01T00-00-00-old.md"
    retained.write_text("keep outside", encoding="utf-8")
    link = {
        "root": root,
        "extensions": root / "extensions",
        "extension": resources.parent,
        "resources": resources,
    }[component]
    suffix = retained.relative_to(link)
    outside = tmp_path / "outside"
    link.rename(outside)
    junction(link, outside)
    try:
        assert not prune_old_extension_resources(root, now=datetime(2026, 10, 22, tzinfo=UTC))
        assert (outside / suffix).read_text(encoding="utf-8") == "keep outside"
    finally:
        link.rmdir()


@pytest.mark.parametrize(
    "name", [r"skills/sub\..\..\outside.md", "skills/file.md:stream", "skills/CON.md"]
)
def test_published_snapshot_rejects_windows_path_aliases(tmp_path, name):
    from corki.memory.agent_artifacts import AgentArtifacts, publish
    from corki.memory.workspace import validate

    files = {name: {"content": "eA==", "mode": "100644"}}
    assert validate(files) is None
    with pytest.raises(ValueError, match="invalid staged memory output paths"):
        publish(tmp_path / "memory", AgentArtifacts(files))
    assert not (tmp_path / "outside.md").exists()


def test_reset_rejects_root_junction_before_database_or_file_changes(tmp_path):
    from corki.memory.reset import MemoryResetError, MemoryResetter, _clear_roots

    outside = tmp_path / "outside"
    outside.mkdir()
    retained = outside / "keep.md"
    retained.write_text("keep", encoding="utf-8")
    root = tmp_path / "memory"
    junction(root, outside)

    class Repository:
        cleared = False

        async def clear_memory_data(self):
            self.cleared = True

    repository = Repository()
    resetter = MemoryResetter(
        roots=(root,), protected_paths=(), repository=repository, database_path=None
    )
    try:
        with pytest.raises(MemoryResetError, match="linked memory root"):
            asyncio.run(resetter.reset())
        assert not repository.cleared
        with pytest.raises(ValueError, match="symlinked memory root"):
            _clear_roots((root,))
        assert retained.read_text(encoding="utf-8") == "keep"
    finally:
        root.rmdir()


@pytest.mark.parametrize("component", ["root", "metadata", "nested"])
def test_git_baseline_does_not_follow_junctions(tmp_path, component):
    from corki.memory import git_baseline

    outside = tmp_path / "outside"
    outside.mkdir()
    retained = outside / "keep.md"
    retained.write_text("keep", encoding="utf-8")
    root = tmp_path / "memory"
    root.mkdir()
    link = {"root": root, "metadata": root / ".git", "nested": root / "nested"}[component]
    if component == "root":
        root.rmdir()
    junction(link, outside)
    try:
        with pytest.raises(ValueError):
            if component == "metadata":
                git_baseline.ensure_layout(root)
            else:
                git_baseline.capture(root)
        assert retained.read_text(encoding="utf-8") == "keep"
        assert list(outside.iterdir()) == [retained]
    finally:
        link.rmdir()


@pytest.mark.parametrize("component", ["root", "extensions", "ad_hoc"])
def test_seed_instructions_never_writes_through_junction(tmp_path, component):
    from corki.memory.consolidation_prompt import seed_extension_instructions

    root = tmp_path / "memory"
    (root / "extensions").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = {"root": root, "extensions": root / "extensions", "ad_hoc": root / "extensions/ad_hoc"}[
        component
    ]
    if component == "root":
        (root / "extensions").rmdir()
    if link.exists():
        link.rmdir()
    junction(link, outside)
    try:
        with pytest.raises(ValueError, match="symbolic link"):
            seed_extension_instructions(root)
        assert list(outside.iterdir()) == []
    finally:
        link.rmdir()
