"""Explicit host control for resetting generated memory, not conversation history."""

import asyncio
import shutil
import stat
from pathlib import Path

from corki.memory.repository import MemoryRepository
from corki.memory.sqlite import SQLiteMemoryRepository
from corki.memory.workspace_lease import MemoryWorkspaceBusy, WorkspaceLeases


class MemoryResetError(RuntimeError):
    """A reset failed; database clearing may already have committed."""

    def __init__(self, message: str, *, database_cleared: bool = False) -> None:
        super().__init__(message)
        self.database_cleared = database_cleared


class MemoryResetter:
    """Serialize a host's reset requests and retain ownership through cancellation."""

    def __init__(
        self,
        *,
        roots: tuple[Path, ...],
        protected_paths: tuple[Path, ...],
        repository: MemoryRepository | None,
        database_path: Path | None,
    ) -> None:
        self._roots = tuple(dict.fromkeys(roots))
        self._protected = protected_paths
        self._repository = repository
        self._database_path = database_path
        self._lock = asyncio.Lock()
        self._active: asyncio.Task[tuple[Path, ...]] | None = None
        self._closed = False

    @property
    def targets(self) -> tuple[Path, ...]:
        """Return exact configured directories for host confirmation, without clearing them."""
        return self._roots

    async def reset(self) -> tuple[Path, ...]:
        """Clear DB memory state, then both directories; cancellation joins started work."""
        async with self._lock:
            if self._closed:
                raise RuntimeError("memory reset controller is closed")
            task = asyncio.create_task(self._run(), name="corki-memory-reset")
            self._active = task
            cancelled = False
            try:
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        cancelled = True
                    except Exception:  # noqa: BLE001 - inspect the owned task below
                        break
                if cancelled:
                    error = None if task.cancelled() else task.exception()
                    raise asyncio.CancelledError from error
                return task.result()
            finally:
                self._active = None

    async def aclose(self) -> None:
        """Reject queued resets and join any reset that already acquired ownership."""
        self._closed = True
        if self._active is not None:
            await asyncio.shield(asyncio.gather(self._active, return_exceptions=True))

    async def _run(self) -> tuple[Path, ...]:
        await asyncio.to_thread(self._validate_targets)
        try:
            leases = await asyncio.to_thread(WorkspaceLeases, self._roots)
        except MemoryWorkspaceBusy as exc:
            raise MemoryResetError(str(exc) + "; no memory state cleared") from exc
        except (OSError, ValueError) as exc:
            raise MemoryResetError(
                "cannot acquire memory workspace ownership; no memory state cleared"
            ) from exc
        try:
            return await self._clear()
        finally:
            leases.close()

    async def _clear(self) -> tuple[Path, ...]:
        repository = self._repository
        owned = repository is None
        cleared = False
        error = None
        try:
            if repository is None:
                if self._database_path is None:
                    raise MemoryResetError("memory reset requires a memory repository")
                repository = await asyncio.to_thread(SQLiteMemoryRepository, self._database_path)
            clear = getattr(repository, "clear_memory_data", None)
            if not callable(clear):
                raise MemoryResetError("memory repository does not support explicit reset")
            await clear()
            cleared = True
        except Exception as exc:
            error = exc
        finally:
            if owned and repository is not None:
                try:
                    await repository.close()
                except Exception as exc:
                    if error is None:
                        error = exc
        if error is not None:
            raise MemoryResetError(
                "memory reset database stage failed; "
                + (
                    "database already cleared, files not cleared"
                    if cleared
                    else "files not cleared"
                ),
                database_cleared=cleared,
            ) from error
        try:
            await asyncio.to_thread(_clear_roots, self._roots)
        except Exception as exc:
            raise MemoryResetError(
                "memory database cleared, but memory files may be only partially removed; "
                "reset did not complete",
                database_cleared=True,
            ) from exc
        return self._roots

    def _validate_targets(self) -> None:
        protected = tuple(path.resolve() for path in self._protected)
        targets = tuple(path.resolve() for path in self._roots)
        for logical, target in zip(self._roots, targets, strict=True):
            if not logical.is_absolute() or target.parent == target:
                raise MemoryResetError("memory reset requires bounded absolute directories")
            if any(target == path or target in path.parents for path in protected):
                raise MemoryResetError(
                    f"refusing broad or database-containing memory root: {logical}"
                )
            if any(target != other and target in other.parents for other in targets):
                raise MemoryResetError("memory reset roots must not overlap")


def _clear_roots(roots: tuple[Path, ...]) -> None:
    for root in roots:
        if root.is_symlink():
            raise ValueError(f"refusing to clear symlinked memory root: {root}")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        for entry in root.iterdir():
            if stat.S_ISDIR(entry.lstat().st_mode):
                shutil.rmtree(entry)
            else:
                entry.unlink()
