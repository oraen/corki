import os

import pytest

from corki.storage.file_lock import open_lock, try_lock, unlock_and_close


def test_lock_does_not_truncate_or_inherit(tmp_path):
    path = tmp_path / "中文 lock"
    path.write_bytes(b"existing")
    first, second = open_lock(path), open_lock(path)
    try:
        assert not os.get_inheritable(first.fileno())
        assert try_lock(first)
        assert not try_lock(second)
    finally:
        second.close()
        unlock_and_close(first)
    assert path.read_bytes() == b"existing"
    successor = open_lock(path)
    assert try_lock(successor)
    unlock_and_close(successor)


def test_lock_rejects_directory(tmp_path):
    with pytest.raises(OSError):
        open_lock(tmp_path)


def test_shared_readers_exclude_writer_until_last_reader_releases(tmp_path):
    path = tmp_path / "shared lock"
    first, second, writer = open_lock(path), open_lock(path), open_lock(path)
    first_locked = second_locked = writer_locked = False
    try:
        first_locked = try_lock(first, shared=True)
        second_locked = try_lock(second, shared=True)
        assert first_locked and second_locked
        assert not try_lock(writer)
        unlock_and_close(first)
        first_locked = False
        assert not try_lock(writer)
        unlock_and_close(second)
        second_locked = False
        writer_locked = try_lock(writer)
        assert writer_locked
        with open_lock(path) as contender:
            assert not try_lock(contender, shared=True)
    finally:
        for file, locked in (
            (first, first_locked),
            (second, second_locked),
            (writer, writer_locked),
        ):
            if not file.closed:
                unlock_and_close(file) if locked else file.close()


def test_lock_rejects_symbolic_link(tmp_path):
    target = tmp_path / "target"
    target.write_bytes(b"preserve")
    link = tmp_path / "link"
    try:
        link.symlink_to(target)
    except OSError as error:
        if os.name == "nt" and error.winerror == 1314:
            pytest.skip("creating test symlink requires Windows developer mode or privilege")
        raise
    with pytest.raises(OSError):
        open_lock(link)
    assert target.read_bytes() == b"preserve"
