"""Keep a database claim owned until dispatch or cancellation cleanup accepts it."""

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, TypeVar

_Claim = TypeVar("_Claim")


async def handoff_claim(
    operation: Coroutine[Any, Any, _Claim],
    *,
    on_cancel: Callable[[_Claim], Awaitable[None]],
    warn: Callable[[str], None],
) -> _Claim:
    task = asyncio.create_task(operation, name="memory-claim")
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await _settle(task)
        if not task.cancelled():
            try:
                claim = task.result()
            except Exception as error:
                warn(f"memory claim failed during cancellation: {error}")
            else:
                # Awaiting cancellation is not rollback of a committed claim.
                # Apply the pipeline's fenced failure path and configured backoff.
                cleanup = asyncio.ensure_future(on_cancel(claim))
                await _settle(cleanup)
                if not cleanup.cancelled() and (error := cleanup.exception()) is not None:
                    warn(f"memory claim cleanup failed (lease will expire): {error}")
        raise


async def _settle(task: asyncio.Future) -> None:
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except Exception:
            break
