"""Native Windows read-only cleanup must preserve directory-link targets."""

import os
import stat
import subprocess

import pytest

from corki.platforms.cleanup import remove_owned_tree

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires Windows attributes and junctions")


def test_owned_tree_removes_readonly_files_without_touching_junction_target(tmp_path):
    root, outside = tmp_path / "owned", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    owned, retained = root / "object", outside / "keep"
    for file in (owned, retained):
        file.write_bytes(b"fixture")
        file.chmod(stat.S_IREAD)
    link = root / "linked"
    subprocess.run(
        f'cmd.exe /d /c mklink /J "{link}" "{outside}"',
        capture_output=True,
        check=True,
        timeout=30,
    )
    try:
        remove_owned_tree(root)
        assert not root.exists()
        assert retained.read_bytes() == b"fixture"
        assert retained.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
    finally:
        retained.chmod(stat.S_IWRITE)
