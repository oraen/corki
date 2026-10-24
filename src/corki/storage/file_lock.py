"""OS-backed advisory locks for owned coordination files; no PID/age inference."""

import errno
import os
import stat
import time
from pathlib import Path
from typing import BinaryIO

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def open_lock(path: Path) -> BinaryIO:
    """Open a regular, non-inheritable lock file without truncating existing data."""
    if os.name == "nt":
        from corki.platforms.windows_file_handles import WindowsFiles

        files = WindowsFiles()
        handle = files.open(path.absolute(), access=0xC0000000, disposition=3)
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        except BaseException:
            files.close(handle)
            raise
    else:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError(f"writer lock is not a regular file: {path}")
        return os.fdopen(descriptor, "r+b")
    except BaseException:
        os.close(descriptor)
        raise


def try_lock(file: BinaryIO, *, shared: bool = False) -> bool:
    """Return False only for actual kernel lock contention."""
    if os.name == "nt":
        from corki.platforms.windows_file_lock import try_lock as windows_try_lock

        return windows_try_lock(file.fileno(), shared=shared)
    try:
        fcntl.flock(file.fileno(), (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        return True
    except OSError as error:
        if error.errno in {errno.EACCES, errno.EAGAIN}:
            return False
        raise


def lock(file: BinaryIO) -> None:
    """Wait for brief coordination operations in a worker, not the event loop."""
    if os.name == "nt":
        # LK_LOCK's ten attempts are not a liveness test. Keep waiting for the
        # actual owner; cancellation of our caller joins this worker.
        while not try_lock(file):
            time.sleep(0.01)
    else:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX)


def unlock_and_close(file: BinaryIO) -> None:
    """Always close the descriptor even if an explicit unlock reports an error."""
    try:
        if os.name == "nt":
            from corki.platforms.windows_file_lock import unlock

            unlock(file.fileno())
        else:
            fcntl.flock(file.fileno(), fcntl.LOCK_UN)
    finally:
        file.close()
