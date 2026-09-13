"""Connection-set-local catalog authority with concurrent readers and fair writers."""

import asyncio
from contextlib import asynccontextmanager


class MCPCatalogRevision:
    def __init__(self):
        self.value = 0
        self._condition = asyncio.Condition()
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0

    @asynccontextmanager
    async def read(self):
        async with self._condition:
            await self._condition.wait_for(lambda: not self._writer and not self._waiting_writers)
            self._readers += 1
        try:
            yield self.value
        finally:
            async with self._condition:
                self._readers -= 1
                self._condition.notify_all()

    @asynccontextmanager
    async def write(self):
        async with self._condition:
            self._waiting_writers += 1
            try:
                await self._condition.wait_for(lambda: not self._writer and not self._readers)
                self._writer = True
            finally:
                self._waiting_writers -= 1
                # Cancelling a queued writer must wake readers it was holding back.
                self._condition.notify_all()
        try:
            yield self
        finally:
            async with self._condition:
                self._writer = False
                self._condition.notify_all()
