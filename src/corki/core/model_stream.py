"""Own one response iterator and tasks racing it against cancellation."""

import asyncio
import logging
from collections.abc import AsyncIterator

from corki.models import ModelCompleted, ModelPort, ModelRequest
from corki.models.types import ModelEvent
from corki.realtime.controller import RealtimeController, RealtimeStop

_LOG = logging.getLogger(__name__)


async def model_events(
    model: ModelPort,
    request: ModelRequest,
    realtime: RealtimeController | None,
    failure: asyncio.Future[BaseException] | None = None,
) -> AsyncIterator[ModelEvent | RealtimeStop]:
    """Yield response/stop events; ordinary user input waits for the next Step.

    No response read or input consumer may outlive this scope. ModelCompleted is
    authoritative: do not wait for the provider to close its iterator afterwards.
    """

    iterator = model.stream(request).__aiter__()
    primary_error: BaseException | None = None
    try:
        while True:
            try:
                live_input = realtime is not None and realtime.active
                if live_input or failure is not None:
                    model_task = asyncio.create_task(anext(iterator), name="corki-model-read")
                    input_task = (
                        asyncio.create_task(realtime.next_stop(), name="corki-stop-read")
                        if live_input
                        else None
                    )
                    tasks = [model_task] + ([input_task] if input_task is not None else [])
                    try:
                        watched = [*tasks, *([failure] if failure is not None else [])]
                        done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        # asyncio.wait does not own its children. Join both even
                        # if this parent is cancelled or one child raises.
                        for task in tasks:
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                    if failure is not None and failure in done:
                        raise failure.result()
                    if input_task is not None and input_task in done:
                        yield input_task.result()
                        return
                    event = model_task.result()
                else:
                    event = await anext(iterator)
            except StopAsyncIteration:
                return
            yield event
            if isinstance(event, ModelCompleted):
                return
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        close = getattr(iterator, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception:
                if primary_error is None:
                    raise
                # A cleanup error must not convert cancellation into TurnFailed
                # or hide the actual provider/storage error.
                _LOG.warning("Response cleanup failed while unwinding", exc_info=True)
