"""Host archive lifecycle, distinct from memory eligibility and forgetting."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from corki.protocol.ids import ThreadId
from corki.sessions.models import ThreadRecord

_LOG = logging.getLogger(__name__)
SHUTDOWN_TIMEOUT_SECONDS = 10


class ThreadArchiveStore(Protocol):
    """Preserve canonical history; mutations must exclude live Thread writers."""

    async def read(self, thread_id: ThreadId) -> ThreadRecord:
        """Read either collection, raising LookupError if the Thread does not exist."""
        ...

    async def archive(self, thread_id: ThreadId) -> ThreadRecord:
        """Atomically archive materialized history, excluding competing writers."""
        ...

    async def unarchive(self, thread_id: ThreadId) -> ThreadRecord:
        """Atomically restore archived history and refresh its source timestamp."""
        ...


class ThreadArchiveController:
    """Close the owning Runtime before changing the durable history collection."""

    def __init__(
        self, store: ThreadArchiveStore, thread_id: ThreadId, close: Callable[[], Awaitable[None]]
    ) -> None:
        self._store, self._thread_id, self._close = store, thread_id, close
        self._lock = asyncio.Lock()

    async def archive(self) -> ThreadRecord:
        """Join an admitted operation even when its host caller is cancelled."""
        async with self._lock:
            task = asyncio.create_task(self._archive(), name="corki-thread-archive")
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not task.cancelled() and task.exception() is not None:
                    _LOG.warning("Archive failed during cancellation", exc_info=task.exception())
                raise

    async def _archive(self) -> ThreadRecord:
        record = await self._store.read(self._thread_id)
        if record.archived_at is not None:
            raise ValueError(f"thread {self._thread_id} is already archived")
        if not record.has_history:
            raise ValueError(f"thread {self._thread_id} has no materialized history")
        try:
            # Runtime.aclose shields its own cleanup. A timeout is not ownership:
            # the store still rejects a writer whose teardown has not finished.
            await asyncio.wait_for(self._close(), SHUTDOWN_TIMEOUT_SECONDS)
        except Exception:
            _LOG.warning("Thread shutdown failed before archive", exc_info=True)
        return await self._store.archive(self._thread_id)

    async def unarchive(self) -> ThreadRecord:
        """Restore source visibility, without implicitly opening a Runtime writer."""
        async with self._lock:
            return await self._store.unarchive(self._thread_id)
