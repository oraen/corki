"""Owned filesystem workers with cooperative cancellation and guarded publication."""

import asyncio
import threading
from collections.abc import Callable
from contextlib import suppress
from contextvars import ContextVar
from typing import ParamSpec, TypeVar

_P = ParamSpec("_P")
_T = TypeVar("_T")


class _ReadOwner:
    def __init__(self):
        self.cancelled = threading.Event()
        self.publication = threading.Lock()

    def cancel(self):
        with self.publication:
            self.cancelled.set()

    def check(self):
        if self.cancelled.is_set():
            raise asyncio.CancelledError


_OWNER: ContextVar[_ReadOwner | None] = ContextVar("skill_read_owner", default=None)


def check_skill_io() -> None:
    owner = _OWNER.get()
    if owner is not None:
        owner.check()


def publish_skill_io(publish: Callable[[], _T]) -> _T:
    """Only a short, non-blocking cache publication belongs inside this boundary."""
    owner = _OWNER.get()
    if owner is None:
        return publish()
    with owner.publication:
        owner.check()
        return publish()


async def run_skill_io(function: Callable[_P, _T], *args: _P.args, **kwargs: _P.kwargs) -> _T:
    """Never abandon a worker on cancellation or let its late failure replace cancellation."""
    owner = _ReadOwner()

    def run():
        token = _OWNER.set(owner)
        try:
            owner.check()
            result = function(*args, **kwargs)
            owner.check()
            return result
        finally:
            _OWNER.reset(token)

    task = asyncio.create_task(asyncio.to_thread(run), name="skill-io")
    interrupted = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            owner.cancel()
            interrupted = interrupted or error
        except Exception:
            if interrupted is None:
                raise
    if interrupted is not None:
        with suppress(Exception, asyncio.CancelledError):
            task.result()
        raise interrupted
    return task.result()
