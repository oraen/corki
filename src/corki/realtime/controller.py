"""Concurrency-safe command channel shared by the CLI and running graph."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from corki.protocol.ids import ItemId, TurnId
from corki.protocol.input_mentions import validate_mentions
from corki.protocol.items import UserMessageItem
from corki.protocol.tools import validate_image_attachments


class RealtimeTurnClosedError(RuntimeError):
    """A steer-only submission cannot enter a turn that has stopped accepting input."""


@dataclass(frozen=True, slots=True)
class RealtimeInput:
    text: str
    item: UserMessageItem | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class RealtimeStop:
    pass


RealtimeCommand = RealtimeInput | RealtimeStop


class RealtimeController:
    """Queue bounded user steering without mutating checkpointed graph state."""

    def __init__(self, max_inputs: int) -> None:
        self._queue: asyncio.Queue[RealtimeCommand] = asyncio.Queue(maxsize=max_inputs + 1)
        self._max_inputs = max_inputs
        self._accepted = 0
        self._turn_id: TurnId | None = None
        self._stop_queued = False
        self._stop_event = asyncio.Event()
        self._accepting = False
        self._unrecorded: dict[ItemId, UserMessageItem] = {}

    @property
    def active(self) -> bool:
        return self._turn_id is not None

    @property
    def stop_requested(self) -> bool:
        return self._stop_queued

    @property
    def unrecorded_items(self) -> tuple[UserMessageItem, ...]:
        """Includes dequeued inputs until their durable append is acknowledged."""

        return tuple(self._unrecorded.values())

    def acknowledge(self, items: tuple[UserMessageItem, ...]) -> None:
        for item in items:
            self._unrecorded.pop(item.id, None)

    def close_input(self) -> None:
        self._accepting = False

    def finish_if_idle(self) -> bool:
        """Atomically choose continuation or close the steer acceptance boundary.

        Like steer's put_nowait, this method must never suspend between checking
        the queue and changing acceptance state.
        """

        if not self._queue.empty():
            return False
        self.close_input()
        return True

    def activate(self, turn_id: TurnId) -> None:
        if self.active:
            raise RuntimeError("a realtime turn is already active")
        if self._unrecorded:
            raise RuntimeError("previous realtime inputs have not been recorded")
        self._turn_id = turn_id
        self._accepting = True
        self._accepted = 0
        self._stop_queued = False
        self._stop_event.clear()
        while not self._queue.empty():
            self._queue.get_nowait()

    def deactivate(self) -> None:
        self.close_input()
        self._turn_id = None
        self._accepted = 0
        self._stop_queued = False
        while not self._queue.empty():
            self._queue.get_nowait()

    async def steer(self, text: str, *, mentions=(), attachments=(), image_positions=()) -> None:
        if not self.active or not self._accepting:
            raise RealtimeTurnClosedError("no realtime turn is accepting input (turn closed)")
        mentions = validate_mentions(mentions)
        attachments = validate_image_attachments(attachments)
        if not image_positions:
            text = text.strip()
        if not text and not mentions and not attachments:
            raise ValueError("realtime input must not be empty")
        if self._accepted >= self._max_inputs:
            raise RuntimeError("realtime input limit reached for this turn")
        assert self._turn_id is not None
        item = UserMessageItem(
            text,
            self._turn_id,
            mentions=mentions,
            attachments=attachments,
            image_positions=image_positions,
        )
        self._accepted += 1
        self._unrecorded[item.id] = item
        self._queue.put_nowait(RealtimeInput(text, item))

    async def stop(self) -> None:
        if self.active and self._accepting and not self._stop_queued:
            # Capacity reserves one slot beyond the input limit, so cancellation
            # is immediate and repeated Ctrl+C requests remain idempotent.
            self._queue.put_nowait(RealtimeStop())
            self._stop_queued = True
            self._stop_event.set()

    async def next_stop(self) -> RealtimeStop:
        """Cancellation can interrupt sampling without consuming queued user input."""
        await self._stop_event.wait()
        return RealtimeStop()

    async def next(self) -> RealtimeCommand:
        return await self._queue.get()

    def take_pending(self) -> tuple[RealtimeCommand, ...]:
        commands: list[RealtimeCommand] = []
        while not self._queue.empty():
            commands.append(self._queue.get_nowait())
        return tuple(commands)
