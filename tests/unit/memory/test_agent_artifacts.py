import base64

import pytest

from corki.memory import workspace
from corki.memory.agent_artifacts import collect, publish


def seed(root):
    (root / "MEMORY.md").write_text("v1\nDetails AKIAABCDEFGHIJKLMNOP")
    (root / "memory_summary.md").write_text("v1\nIndex")
    (root / "source.txt").write_text("read-only")
    return workspace.capture(root)


@pytest.mark.parametrize("change", ["edit", "delete", "add", "symlink", "summary", "missing"])
def test_collect_rejects_invalid_artifacts_and_changed_source(tmp_path, change):
    sampled = seed(tmp_path)
    if change == "edit":
        (tmp_path / "source.txt").write_text("changed")
    elif change == "delete":
        (tmp_path / "source.txt").unlink()
    elif change == "add":
        (tmp_path / "extra.txt").write_text("unexpected source")
    elif change == "symlink":
        (tmp_path / "link").symlink_to(tmp_path / "source.txt")
    elif change == "summary":
        (tmp_path / "memory_summary.md").write_text("not versioned")
    else:
        (tmp_path / "MEMORY.md").unlink()
    with pytest.raises(ValueError):
        collect(tmp_path, sampled)


def test_collect_preserves_binary_and_redacts_text(tmp_path):
    sampled = seed(tmp_path)
    (tmp_path / "skills/demo").mkdir(parents=True)
    (tmp_path / "skills/demo/image.bin").write_bytes(b"\xff\x00")
    (tmp_path / "skills/demo/run.sh").write_text("echo safe")
    (tmp_path / "skills/demo/run.sh").chmod(0o700)
    artifacts = collect(tmp_path, sampled)
    assert "source.txt" not in artifacts.files
    assert b"AKIAABCDEFGHIJKLMNOP" not in base64.b64decode(artifacts.files["MEMORY.md"]["content"])
    assert base64.b64decode(artifacts.files["skills/demo/image.bin"]["content"]) == b"\xff\x00"
    assert artifacts.files["skills/demo/run.sh"]["mode"] == "100755"


@pytest.mark.parametrize("direction", ["file_to_directory", "directory_to_file"])
def test_publish_supports_skill_output_path_shape_changes(tmp_path, direction):
    root, staged = tmp_path / "live", tmp_path / "staged"
    root.mkdir()
    staged.mkdir()
    seed(root)
    sampled = seed(staged)
    before, after = (
        ("skills/item", "skills/item/guide.md")
        if direction == "file_to_directory"
        else ("skills/item/guide.md", "skills/item")
    )
    (root / before).parent.mkdir(parents=True, exist_ok=True)
    (root / before).write_text("old")
    (staged / after).parent.mkdir(parents=True, exist_ok=True)
    (staged / after).write_text("new")
    publish(root, collect(staged, sampled))
    assert (root / after).read_text() == "new"
    assert (root / "source.txt").read_text() == "read-only"
