"""One terminal reader, with modal priority over the ordinary composer."""

import asyncio
import logging
from dataclasses import dataclass

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class QueuedInput:
    """An explicit next-turn submission, not steering or model-facing metadata."""

    text: str


@dataclass(frozen=True, slots=True)
class CycleModeInput:
    """Composer control, never a user message or a queued submission."""


def submission_text(value: str | QueuedInput | CycleModeInput) -> str:
    if isinstance(value, CycleModeInput):
        return ""
    return (value.text if isinstance(value, QueuedInput) else value).strip()


class InputInterrupted(Exception):
    """A keyboard interrupt delivered by an owned child reader, not a loop abort."""


async def join_inputs(*tasks: asyncio.Task | None) -> None:
    owned = tuple(task for task in tasks if task is not None)
    for task in owned:
        if not task.done() and not task.cancelling():
            task.cancel()
    joined = asyncio.gather(*owned, return_exceptions=True)
    cancelled = False
    while not joined.done():
        try:
            await asyncio.shield(joined)
        except asyncio.CancelledError:
            cancelled = True
    for result in joined.result():
        if isinstance(result, Exception) and not isinstance(result, (EOFError, InputInterrupted)):
            _LOG.warning("Input task cleanup failed: %s", type(result).__name__)
    if cancelled:
        raise asyncio.CancelledError


class InputOwner:
    def __init__(self, ui):
        self.ui = ui
        self._lock = asyncio.Lock()
        self._approval_lock = asyncio.Lock()
        self._ordinary = asyncio.Event()
        self._ordinary.set()
        self._modals = 0
        self._reader: asyncio.Task | None = None

    async def _read(self):
        try:
            return await self.ui.read_message()
        except KeyboardInterrupt as error:
            raise InputInterrupted from error

    async def read_message(self):
        while True:
            await self._ordinary.wait()
            async with self._lock:
                if self._modals:
                    continue
                reader = asyncio.create_task(self._read(), name="corki-composer-input")
                self._reader = reader
                try:
                    return await asyncio.shield(reader)
                except asyncio.CancelledError:
                    task = asyncio.current_task()
                    if task is not None and task.cancelling():
                        raise
                    # A modal interrupted only the UI reader, not this consumer.
                finally:
                    try:
                        await join_inputs(reader)
                    finally:
                        self._reader = None

    async def elicit(self, request):
        # Keep approval arrival order while allowing the composer to keep typing
        # until its idle deadline. Cancellation here owns no terminal reader.
        async with self._approval_lock:
            overlay = getattr(self.ui, "try_overlay_approval", None)
            if overlay is not None:
                result = overlay(request)
                if result is not None:
                    return await result
            wait_idle = getattr(self.ui, "wait_for_approval_idle", None)
            if wait_idle is not None:
                await wait_idle()
            return await self._modal(self.ui.read_elicitation, request)

    async def ask_user(self, request):
        return await self._modal(self.ui.read_user_input, request)

    async def select_model(self, current):
        return await self._modal(self.ui.read_model, current)

    async def _modal(self, read, request):
        self._modals += 1
        self._ordinary.clear()
        reader = self._reader
        if reader is not None and not reader.done() and not reader.cancelling():
            reader.cancel()
        try:
            async with self._lock:
                return await read(request)
        finally:
            self._modals -= 1
            if not self._modals:
                self._ordinary.set()
