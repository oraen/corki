"""Join a started blocking tool worker before reporting cancellation."""

import asyncio
from collections.abc import Callable
from contextlib import suppress
from typing import ParamSpec, TypeVar

_P = ParamSpec("_P")
_T = TypeVar("_T")


async def joined_tool_work(function: Callable[_P, _T], *args: _P.args) -> _T:
    """A filesystem side effect may finish after cancellation, but never after its Turn."""
    worker = asyncio.create_task(asyncio.to_thread(function, *args))
    cancelled = False
    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled:
        with suppress(Exception, asyncio.CancelledError):
            worker.result()
        raise asyncio.CancelledError
    return worker.result()
