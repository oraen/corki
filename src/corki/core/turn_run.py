"""Owned turn task, independent of a possibly paused event consumer."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal

from corki.protocol.events import RuntimeEvent
from corki.protocol.ids import TurnId


class TurnRun:
    def __init__(self, turn_id: TurnId, queue_size: int) -> None:
        self.queue: asyncio.Queue[RuntimeEvent] = asyncio.Queue(maxsize=queue_size)
        self.done = asyncio.Event()
        self.task: asyncio.Task[None] | None = None
        self.turn_id = turn_id
        self.started = False
        self.finishing = False
        self.cancel_requested = False
        self.cancel_reason: Literal["interrupted", "replaced"] | None = None
        self.terminal: RuntimeEvent | None = None
        self.error: BaseException | None = None
        self.cleanup_error: BaseException | None = None

    def start(self, operation: Callable[[TurnRun], Awaitable[RuntimeEvent]]) -> None:
        async def owned() -> None:
            self.started = True
            try:
                self.terminal = await operation(self)
            except BaseException as exc:
                self.error = exc
            finally:
                self.finishing = True
                # Terminal delivery never needs a free data queue slot.
                self.done.set()

        self.task = asyncio.create_task(owned(), name=f"corki-turn-{self.turn_id}")

    def cancel(self, *, reason: Literal["interrupted", "replaced"] = "interrupted") -> None:
        if self.finishing or self.cancel_requested:
            return
        self.cancel_requested = True
        self.cancel_reason = reason
        # Cancelling a task before its first instruction prevents its finally
        # block from running. The operation checks this flag on startup instead.
        if self.started and self.task is not None:
            self.task.cancel()

    async def join(self) -> None:
        """Cleanup wait: repeated caller cancellation cannot orphan owned work."""

        assert self.task is not None
        while not self.task.done():
            try:
                await asyncio.shield(self.task)
            except asyncio.CancelledError:
                continue

    def result(self) -> RuntimeEvent:
        if self.error is not None:
            raise self.error
        assert self.terminal is not None
        return self.terminal
