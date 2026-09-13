"""Wait for advertised handlers without waking durable completed/unknown calls."""

import asyncio

from corki.protocol.tools import ToolExposure
from corki.tools.base import ToolReadiness


class StepToolReadiness:
    def __init__(self, repository, thread_id, turn_id, snapshot):
        self.repository, self.thread_id, self.turn_id = repository, thread_id, turn_id
        self.snapshot = snapshot
        self._saved = None
        self._lock = asyncio.Lock()

    async def __call__(self, call, spec):
        tool = self.snapshot.get(call.name)
        if (
            spec is None
            or spec.exposure == ToolExposure.HIDDEN
            or spec != self.snapshot.spec(call.name)
            or not isinstance(tool, ToolReadiness)
        ):
            return
        async with self._lock:
            if self._saved is None:
                self._saved = {
                    result.call_id
                    for result in await self.repository.load_turn_tool_outcomes(
                        self.thread_id, self.turn_id
                    )
                }
        if call.id not in self._saved:
            await tool.wait_until_ready()
