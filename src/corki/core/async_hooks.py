"""Session-owned asynchronous effects, independent of the active Turn's sink."""

import asyncio
import logging
from collections import deque
from copy import deepcopy

_LOG = logging.getLogger(__name__)


class AsyncHooks:
    def __init__(self, *, limiter=None):
        self._tasks = {}
        self._results = deque()
        self._seen = set()
        self._limit = limiter if limiter is not None else asyncio.Semaphore(8)
        self._closed = False
        self._close_task = None

    def owns(self, key, request):
        entry = self._tasks.get(key)
        return entry is not None and entry[0] == request

    def start(self, key, request, execute):
        if self._closed:
            raise asyncio.CancelledError
        if key in self._tasks:
            raise RuntimeError("async hook already scheduled")

        async def run():
            async with self._limit:
                if self._closed:
                    return
                result = await execute()
                if not self._closed:
                    self.publish(key, result)

        identity = deepcopy(request)
        task = asyncio.create_task(run(), name=f"corki-async-hook:{key}")
        self._tasks[key] = (identity, task)

        def completed(done):
            self._tasks.pop(key, None)
            if not done.cancelled() and (error := done.exception()) is not None:
                # A journal failure leaves an unknown claim; never retry the effect.
                _LOG.error("Asynchronous hook failed: %s", key, exc_info=error)

        task.add_done_callback(completed)

    def publish(self, key, result):
        if not self._closed and key not in self._seen:
            self._seen.add(key)
            self._results.append((key, result))

    def take(self):
        return self._results.popleft() if self._results else None

    def peek(self):
        return self._results[0] if self._results else None

    def pending(self):
        return tuple(self._results)

    def acknowledge(self, key):
        if not self._results or self._results[0][0] != key:
            raise RuntimeError("Asynchronous hook completion order changed")
        self._results.popleft()

    def acknowledge_selected(self, key):
        """Remove a journal-selected result while preserving unselected completions."""
        entry = next((entry for entry in self._results if entry[0] == key), None)
        if entry is None:
            raise RuntimeError("Asynchronous hook completion disappeared")
        self._results.remove(entry)

    def close_admission(self):
        self._closed = True

    async def aclose(self):
        self.close_admission()
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(), name="corki-async-hooks-close")
        await asyncio.shield(self._close_task)

    async def _close(self):
        tasks = tuple(task for _, task in self._tasks.values())
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._results.clear()
        self._seen.clear()
