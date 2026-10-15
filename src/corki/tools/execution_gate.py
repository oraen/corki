"""Fair parallel/exclusive admission, entered only after tool readiness."""

import asyncio
from collections import deque
from contextlib import asynccontextmanager


class ExecutionGate:
    def __init__(self):
        self._readers = 0
        self._writer = False
        self._waiting = deque()

    def _wake(self):
        while self._waiting and not self._writer:
            ready, parallel = self._waiting[0]
            if not parallel and self._readers:
                break
            self._waiting.popleft()
            if parallel:
                self._readers += 1
            else:
                self._writer = True
            ready.set_result(None)

    @asynccontextmanager
    async def enter(self, *, parallel):
        ready = asyncio.get_running_loop().create_future()
        waiter = (ready, parallel)
        self._waiting.append(waiter)
        self._wake()
        try:
            await asyncio.shield(ready)
            yield
        finally:
            if not ready.done():
                self._waiting.remove(waiter)
            elif parallel:
                self._readers -= 1
            else:
                self._writer = False
            self._wake()
