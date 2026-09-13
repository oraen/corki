"""Lifetime ownership of one canonical Thread across Runtime instances/processes."""

import asyncio
import logging
import re
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO

from corki.protocol.ids import ThreadId
from corki.storage.file_lock import lock, open_lock, try_lock, unlock_and_close

_LOG = logging.getLogger(__name__)
_LOCK_NAME = re.compile(r"[0-9a-f]{64}\.lock\Z")


class ThreadWriterConflict(RuntimeError):
    """Another live owner prevents opening this Thread for mutation."""


class WriterLockCoordinator:
    """Serialize lockfile cleanup with acquisition, scoped to the canonical database."""

    def __init__(self, database_path: Path) -> None:
        database = database_path.resolve()
        self.directory = database.with_name(database.name + ".thread-writer-locks")
        self._cleanup_attempted = False

    def _coordination(self) -> BinaryIO:
        if self.directory.is_symlink():
            raise OSError("writer lock directory cannot be a symbolic link")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        file = open_lock(self.directory / ".coordination.lock")
        try:
            lock(file)
            return file
        except BaseException:
            file.close()
            raise

    def acquire(self, thread_id: ThreadId) -> "WriterLockGuard":
        """Reject a live writer before opening durable history for mutation."""
        coordination = self._coordination()
        guard = None
        try:
            if not self._cleanup_attempted:
                self._cleanup_attempted = True
                try:
                    self._remove_stale()
                except OSError:
                    _LOG.warning("Failed to clean stale thread writer locks", exc_info=True)
            # Thread IDs are opaque in Corki. Hashing preserves their identity
            # without allowing a host-supplied ID to escape the owned directory.
            name = sha256(str(thread_id).encode("utf-8")).hexdigest() + ".lock"
            path = self.directory / name
            file = open_lock(path)
            try:
                if not try_lock(file):
                    raise ThreadWriterConflict(f"thread {thread_id} already has an active writer")
                guard = WriterLockGuard(self, path, file)
                return guard
            except BaseException:
                file.close()
                raise
        finally:
            try:
                unlock_and_close(coordination)
            except BaseException:
                if guard is not None:
                    guard.close()
                raise

    def _remove_stale(self) -> None:
        for path in self.directory.iterdir():
            if not _LOCK_NAME.fullmatch(path.name) or path.is_symlink():
                continue
            try:
                file = open_lock(path)
                try:
                    available = try_lock(file)
                    if available:
                        unlock_and_close(file)
                finally:
                    if not file.closed:
                        file.close()
                if available:
                    path.unlink(missing_ok=True)
            except OSError:
                _LOG.warning("Failed to inspect stale writer lock %s", path, exc_info=True)


class WriterLockGuard:
    """Own one kernel lock until coordinated close; never unlink a live inode."""

    def __init__(self, coordinator: WriterLockCoordinator, path: Path, file: BinaryIO) -> None:
        self._coordinator, self.path, self._file = coordinator, path, file

    def close(self) -> None:
        """Release the kernel lock and remove its pathname under coordination."""
        if self._file is None:
            return
        file, self._file = self._file, None
        coordination = None
        try:
            coordination = self._coordinator._coordination()
            unlock_and_close(file)
            self.path.unlink(missing_ok=True)
        except OSError:
            # Native guard destruction warns if cleanup cannot be coordinated.
            # Release the descriptor; a later owner can safely remove the stale file.
            _LOG.warning("Failed to clean thread writer lock %s", self.path, exc_info=True)
        finally:
            if not file.closed:
                file.close()
            if coordination is not None:
                unlock_and_close(coordination)


async def _join(task: asyncio.Task) -> asyncio.CancelledError | None:
    cancelled = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            cancelled = error
        except Exception:
            break
    return cancelled


class ThreadWriterLease:
    """Async owner which cannot abandon a guard returned during cancellation."""

    def __init__(self, database_path: Path, thread_id: ThreadId) -> None:
        self._coordinator = WriterLockCoordinator(database_path)
        self._thread_id = thread_id
        self._guard: WriterLockGuard | None = None
        self._lock = asyncio.Lock()

    @property
    def held(self) -> bool:
        return self._guard is not None

    async def acquire(self) -> None:
        async with self._lock:
            if self._guard is not None:
                return
            task = asyncio.create_task(
                asyncio.to_thread(self._coordinator.acquire, self._thread_id)
            )
            cancelled = await _join(task)
            try:
                guard = task.result()
            except BaseException as error:
                if cancelled is not None:
                    raise cancelled from error
                raise
            if cancelled is not None:
                cleanup = asyncio.create_task(asyncio.to_thread(guard.close))
                await _join(cleanup)
                try:
                    cleanup.result()
                except BaseException as error:
                    raise cancelled from error
                raise cancelled
            self._guard = guard

    async def aclose(self) -> None:
        async with self._lock:
            guard, self._guard = self._guard, None
            if guard is None:
                return
            task = asyncio.create_task(asyncio.to_thread(guard.close))
            cancelled = await _join(task)
            try:
                task.result()
            except BaseException as error:
                if cancelled is not None:
                    raise cancelled from error
                raise
            if cancelled is not None:
                raise cancelled
