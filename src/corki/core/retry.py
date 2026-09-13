"""Backoff owned by a checkpointed sampling retry node."""

import asyncio
import math

from corki.models.backoff import backoff
from corki.models.failure import ModelFailure
from corki.realtime.controller import RealtimeController, RealtimeStop


def retry_delay(failure: ModelFailure, base: float) -> float:
    delay = failure.retry_after_seconds
    if delay is not None and math.isfinite(delay) and delay >= 0:
        return delay
    return backoff(base, failure.retries_used)


async def wait_retry(delay: float, realtime: RealtimeController | None) -> RealtimeStop | None:
    sleep = asyncio.create_task(asyncio.sleep(delay), name="corki-retry-backoff")
    command = (
        asyncio.create_task(realtime.next_stop(), name="corki-retry-stop")
        if realtime is not None and realtime.active
        else None
    )
    tasks = [sleep, *([command] if command is not None else [])]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        return command.result() if command is not None and command in done else None
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
