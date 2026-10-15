"""Kernel ownership, safe stale cleanup and release/reacquisition."""

from hashlib import sha256

import pytest

from corki.protocol.ids import new_thread_id
from corki.storage.thread_writer import ThreadWriterConflict, WriterLockCoordinator


def test_competing_coordinators_preserve_live_locks_and_remove_stale_files(tmp_path):
    primary = WriterLockCoordinator(tmp_path / "history.db")
    thread = new_thread_id()
    owner = primary.acquire(thread)
    stale = primary.directory / (sha256(b"stale").hexdigest() + ".lock")
    stale.touch()
    unrelated = primary.directory / "user-note.txt"
    unrelated.write_text("preserve")
    secondary = WriterLockCoordinator(tmp_path / "history.db")
    other = secondary.acquire(new_thread_id())
    try:
        assert not stale.exists() and owner.path.exists()
        with pytest.raises(ThreadWriterConflict, match="active writer"):
            secondary.acquire(thread)
        owner.close()
        assert not owner.path.exists()
        successor = secondary.acquire(thread)
        successor.close()
    finally:
        other.close()
        owner.close()
    assert {path.name for path in primary.directory.iterdir()} == {
        ".coordination.lock",
        "user-note.txt",
    }
    assert unrelated.read_text() == "preserve"
