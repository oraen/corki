"""Desktop connectivity notifications; retry timing belongs to the Runtime."""

import asyncio

import httpx

from corki.models.base import ModelError, ModelErrorKind


class ModelCallWatch:
    WARNING_FAILURES = 30

    def __init__(self, notify, *, warning_seconds=30.0):
        self.notify = notify
        self.warning_seconds = warning_seconds
        self.last_activity = None
        self.failures = 0
        self.warning = False
        self.warning_reason = None
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
                    and not self.closed and not self.warning):
                await self.set_warning(True, "failure" if self.failures else "idle")
        self.notification = asyncio.create_task(notify())

    async def set_warning(self, value, reason="failure"):
        reason = reason if value else None
        if self.warning != value or self.warning_reason != reason:
            self.warning = value
            self.warning_reason = reason
            await self.notify(value)

    async def wait(self, awaitable):
        # Never time out Runtime backoff or replace HTTPX's own I/O timeouts.
        # In particular, connection retries may legitimately wait indefinitely.
        return await awaitable

    async def activity(self):
        if self.closed:
            return
        # Bytes/lines prove the connection recovered. Other error counts only
        # reset after completion (a 200 SSE error is still a model failure).
        if self.warning_reason == "connection":
            self.failures = 0
            self.last_error = None
        self.last_activity = asyncio.get_running_loop().time()
        if self.warning_reason == "connection" or self.failures < self.WARNING_FAILURES:
            await self.set_warning(False)
        self.schedule_warning()

    async def failure(self, error):
        if self.closed:
            return
        # The transport may wrap the same physical failure in a ModelError.
        current = error
        while current is not None:
            if current is self.last_error:
                return
            current = current.__cause__
        self.last_error = error
        self.failures += 1
        if isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout)) or (
            isinstance(error, ModelError) and error.kind == ModelErrorKind.CONNECTION
        ):
            await self.set_warning(True, "connection")
        elif isinstance(error, ModelError) and not error.retryable:
            # Preserve recoverable context/image metadata for the core handler.
            if error.kind in (ModelErrorKind.CONTEXT_WINDOW, ModelErrorKind.INVALID_IMAGE):
                return
            await self.set_warning(False)
            raise ModelError(
                "模型调用失败", kind=error.kind, status_code=error.status_code,
                usage=error.usage, response_id=error.response_id,
            ) from error
        elif self.failures >= self.WARNING_FAILURES or self.warning:
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
