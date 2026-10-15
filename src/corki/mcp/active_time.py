"""Counted human-wait pauses for an MCP client's active operation deadlines."""

import asyncio
from contextlib import asynccontextmanager, contextmanager


class ActiveTime:
    def __init__(self) -> None:
        self._pauses = 0
        self._budgets: dict[asyncio.Timeout, float] = {}
        self._paused_at: float | None = None
        self._paused_seconds = 0.0

    def time(self) -> float:
        now = self._paused_at
        if now is None:
            now = asyncio.get_running_loop().time()
        return now - self._paused_seconds

    @asynccontextmanager
    async def timeout(self, seconds: float):
        async with asyncio.timeout(None) as budget:
            self._budgets[budget] = seconds
            if not self._pauses:
                budget.reschedule(asyncio.get_running_loop().time() + seconds)
            try:
                yield
            finally:
                self._budgets.pop(budget, None)

    @contextmanager
    def pause(self):
        if self._pauses == 0:
            now = asyncio.get_running_loop().time()
            self._paused_at = now
            for budget in self._budgets:
                deadline = budget.when()
                if deadline is not None and not budget.expired() and deadline > now:
                    self._budgets[budget] = deadline - now
                    budget.reschedule(None)
        self._pauses += 1
        try:
            yield
        finally:
            self._pauses -= 1
            if not self._pauses:
                now = asyncio.get_running_loop().time()
                assert self._paused_at is not None
                self._paused_seconds += now - self._paused_at
                self._paused_at = None
                for budget, remaining in self._budgets.items():
                    if budget.when() is None and not budget.expired():
                        budget.reschedule(now + remaining)
