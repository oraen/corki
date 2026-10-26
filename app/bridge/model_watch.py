"""Desktop model failure budget, isolated from tools and user interactions."""

import asyncio
import inspect

from corki.models.base import ModelError


class ModelCallWatch:
    WARNING_FAILURES = 30
    FAILURE_FAILURES = 60

    def __init__(self, notify, *, warning_seconds=30.0, failure_seconds=60.0):
        self.notify = notify
        self.warning_seconds = warning_seconds
        self.failure_seconds = failure_seconds
        self.last_activity = None
        self.failures = 0
        self.warning = False
        self.warning_reason = None
        self.expired = False
        self.last_error = None
        self.timer = None
        self.notification = None
        self.closed = False
        self.generation = 0

    def begin(self):
        if self.closed:
            return
        if self.last_activity is None:
            self.last_activity = asyncio.get_running_loop().time()
            self.schedule_warning()

    def schedule_warning(self):
        self.generation += 1
        if self.timer:
            self.timer.cancel()
        if not self.warning and not self.closed:
            loop = asyncio.get_running_loop()
            self.timer = loop.call_at(
                self.last_activity + self.warning_seconds, self.warn, self.generation
            )

    def warn(self, generation):
        self.timer = None
        async def notify():
            if (generation == self.generation and self.last_activity is not None
                    and not self.closed and not self.warning and not self.expired):
                await self.set_warning(True, "failure" if self.failures else "idle")
        self.notification = asyncio.create_task(notify())

    async def set_warning(self, value, reason="failure"):
        reason = reason if value else None
        if self.warning != value or self.warning_reason != reason:
            self.warning = value
            self.warning_reason = reason
            await self.notify(value)

    async def fail(self):
        self.generation += 1
        self.expired = True
        if self.timer:
            self.timer.cancel()
        await self.set_warning(False)
        raise ModelError("模型调用失败", retryable=False)

    async def wait(self, awaitable):
        if self.closed:
            return await awaitable
        if self.expired:
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise ModelError("模型调用失败", retryable=False)
        if self.last_activity is None:
            return await awaitable
        timeout = asyncio.timeout_at(self.last_activity + self.failure_seconds)
        try:
            async with timeout:
                return await awaitable
        except TimeoutError:
            if not timeout.expired():
                raise
            await self.fail()

    async def activity(self):
        if self.closed:
            return
        # Bytes/lines prove responsiveness. Only a completed model response
        # resets the consecutive failure count (a 200 SSE error is still a failure).
        self.last_activity = asyncio.get_running_loop().time()
        if self.failures < self.WARNING_FAILURES:
            await self.set_warning(False)
        self.schedule_warning()

    async def failure(self, error):
        if self.closed:
            return
        if self.expired:
            raise ModelError("模型调用失败", retryable=False)
        # The transport may wrap the same physical failure in a ModelError.
        current = error
        while current is not None:
            if current is self.last_error:
                return
            current = current.__cause__
        self.last_error = error
        self.failures += 1
        if self.failures >= self.FAILURE_FAILURES:
            await self.fail()
        if self.failures >= self.WARNING_FAILURES or self.warning:
            await self.set_warning(True, "failure")

    async def complete(self):
        self.generation += 1
        self.last_activity = None
        self.failures = 0
        self.last_error = None
        if self.timer:
            self.timer.cancel()
            self.timer = None
        await self.set_warning(False)

    async def close(self):
        self.generation += 1
        self.closed = True
        if self.timer:
            self.timer.cancel()
        if self.notification:
            await asyncio.gather(self.notification, return_exceptions=True)
        await self.set_warning(False)
