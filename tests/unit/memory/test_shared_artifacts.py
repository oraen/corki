import os

import pytest

from corki.memory.artifacts import (
    remove_memory_symlinks,
    remove_workspace_diff,
    validate_shared_artifacts,
    write_workspace_diff,
)


def seed(tmp_path):
    root = tmp_path / "memories"
    root.mkdir()
    (root / "MEMORY.md").write_text("worker bytes without version header")
    (root / "memory_summary.md").write_text("v1\nsummary")
    return root


@pytest.mark.parametrize("hidden", [False, True])
@pytest.mark.parametrize("directory", [False, True])
def test_validation_unlinks_links_but_never_follows_targets(tmp_path, hidden, directory):
    root = seed(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / "preserve.txt"
    protected.write_text("outside survives")
    parent = root / (".hidden" if hidden else "notes")
    parent.mkdir()
    link = parent / "alias"
    link.symlink_to(outside if directory else protected, target_is_directory=directory)
    before = (root / "MEMORY.md").read_bytes()
    with pytest.raises(ValueError, match="removed symbolic links"):
        validate_shared_artifacts(root)
    assert not link.is_symlink() and not link.exists()
    assert protected.read_text() == "outside survives"
    assert (root / "MEMORY.md").read_bytes() == before
    validate_shared_artifacts(root)


def test_failed_worker_cleanup_preserves_ordinary_writes_without_validating_success(tmp_path):
    root = tmp_path / "memories"
    root.mkdir()
    partial = root / "partial.txt"
    partial.write_text("acknowledged side effect")
    target = tmp_path / "external.txt"
    target.write_text("not a memory artifact")
    (root / "alias").symlink_to(target)
    assert remove_memory_symlinks(root) == 1
    assert partial.read_text() == "acknowledged side effect"
    assert target.read_text() == "not a memory artifact"
    assert not (root / "MEMORY.md").exists()
    assert not (root / ".consolidation-baseline.json").exists()


def test_generated_diff_cleanup_preserves_memory_and_metadata(tmp_path):
    root = seed(tmp_path)
    metadata = root / ".git"
    metadata.mkdir()
    (metadata / "sentinel").write_text("keep metadata")
    write_workspace_diff(root, "generated prompt")
    assert (root / "phase2_workspace_diff.md").read_text() == "generated prompt"
    remove_workspace_diff(root)
    remove_workspace_diff(root)
    assert root.is_dir() and (root / "MEMORY.md").is_file()
    assert (metadata / "sentinel").read_text() == "keep metadata"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFO")
def test_shared_validation_uses_native_artifact_checks_not_legacy_snapshot_type_rules(tmp_path):
    root = seed(tmp_path)
    os.mkfifo(root / "non_artifact_pipe")
    # Native validation checks links and the two required artifacts. Its Git
    # baseline ignores this FIFO; Corki's baseline migration is tracked separately.
    validate_shared_artifacts(root)
