"""OS-owned locks: process exit releases ownership, without PID reuse assumptions."""

from pathlib import Path

from corki.storage.file_lock import open_lock, try_lock, unlock_and_close


class FileLock:
    def __init__(self, path: Path):
        self.path = path
        self.file = None

    def acquire(self) -> bool:
        if self.file is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open_lock(self.path)
        try:
            acquired = try_lock(handle)
        except BaseException:
            handle.close()
            raise
        if not acquired:
            handle.close()
            return False
        self.file = handle
        return True

    def close(self):
        if self.file is not None:
            handle = self.file
            self.file = None
            unlock_and_close(handle)
