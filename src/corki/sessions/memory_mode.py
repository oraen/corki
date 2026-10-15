"""Host-owned, durable per-thread memory source control, independent of generation."""

import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID

from corki.protocol.ids import ThreadId
from corki.protocol.memory import ThreadMemoryMode
from corki.sessions.repository import SessionRepository


class ThreadMemoryController:
    """Serialize host metadata updates and drain admitted work before storage closes."""

    def __init__(
        self,
        repository: SessionRepository,
        current_thread_id: ThreadId,
        ensure_current: Callable[[], Awaitable[None]],
    ) -> None:
        self._repository = repository
        self._current = current_thread_id
        self._ensure_current = ensure_current
        self._lock = asyncio.Lock()
        self._active: asyncio.Task[None] | None = None
        self._closed = False

    async def set_mode(self, mode: ThreadMemoryMode | str, *, thread_id: str | None = None) -> None:
        """Change current/stored metadata, materializing only this runtime's own thread."""
        try:
            selected = ThreadMemoryMode(mode)
        except (ValueError, TypeError) as exc:
            raise ValueError("memory mode must be enabled or disabled") from exc
        try:
            target = ThreadId(str(UUID(self._current if thread_id is None else thread_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("invalid thread id") from exc
        async with self._lock:
            if self._closed:
                raise RuntimeError("thread memory controller is closed")
            setter = getattr(self._repository, "set_thread_memory_mode", None)
            if not callable(setter):
                raise RuntimeError("session repository does not support thread memory mode")

            async def update() -> None:
                if target == self._current:
                    await self._ensure_current()
                if not await setter(target, selected):
                    raise LookupError(f"thread not found: {target}")

            task = asyncio.create_task(update(), name="corki-thread-memory-mode")
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
                task.result()
            finally:
                self._active = None

    def close_admission(self) -> None:
        """Stop queued writes immediately, before the runtime joins its active turn."""
        self._closed = True

    async def aclose(self) -> None:
        """Reject queued updates and wait for the admitted update without cancelling it."""
        self.close_admission()
        if self._active is not None:
            await asyncio.shield(asyncio.gather(self._active, return_exceptions=True))
