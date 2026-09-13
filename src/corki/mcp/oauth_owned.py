"""Drain OAuth side effects and cleanup before releasing their cancelling owner."""

import asyncio
from contextlib import suppress


async def join_oauth_task(task):
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled:
        with suppress(Exception, asyncio.CancelledError):
            task.result()
        raise asyncio.CancelledError
    return task.result()
