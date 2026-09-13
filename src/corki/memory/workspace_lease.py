"""Kernel ownership of shared memory writes, outside the roots reset removes."""

from pathlib import Path

from corki.storage.file_lock import open_lock, try_lock, unlock_and_close


class MemoryWorkspaceBusy(RuntimeError):
    pass


class WorkspaceLeases:
    def __init__(self, roots: tuple[Path, ...]):
        self._files = []
        try:
            for root in sorted({root.resolve() for root in roots}):
                if root.parent == root:
                    raise ValueError("memory workspace lease requires a bounded root")
                root.parent.mkdir(parents=True, exist_ok=True)
                file = open_lock(root.parent / f".{root.name}.corki-memory.lock")
                try:
                    acquired = try_lock(file)
                except BaseException:
                    file.close()
                    raise
                if not acquired:
                    file.close()
                    raise MemoryWorkspaceBusy(f"memory workspace busy: {root}")
                self._files.append(file)
        except BaseException:
            self.close()
            raise

    def close(self):
        error = None
        while self._files:
            try:
                unlock_and_close(self._files.pop())
            except OSError as exc:
                error = error or exc
        if error is not None:
            raise error
