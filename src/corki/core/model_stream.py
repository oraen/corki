"""Own one response iterator and any tasks racing it against user steering."""

import asyncio
import logging
from collections.abc import AsyncIterator

from corki.models import ModelCompleted, ModelPort, ModelRequest
from corki.models.types import ModelEvent
from corki.realtime.controller import RealtimeCommand, RealtimeController

_LOG = logging.getLogger(__name__)


async def model_events(
    model: ModelPort,
    request: ModelRequest,
    realtime: RealtimeController | None,
) -> AsyncIterator[ModelEvent | RealtimeCommand]:
    """Yield response/steering events; the caller must explicitly close this generator.

    No response read or input consumer may outlive this scope. ModelCompleted is
    authoritative: do not wait for the provider to close its iterator afterwards.
    """

    iterator = model.stream(request).__aiter__()
    primary_error: BaseException | None = None
    try:
        while True:
            try:
                if realtime is not None and realtime.active:
                    model_task = asyncio.create_task(anext(iterator), name="corki-model-read")
                    input_task = asyncio.create_task(realtime.next(), name="corki-steering-read")
                    tasks = (model_task, input_task)
                    try:
                        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        # asyncio.wait does not own its children. Join both even
                        # if this parent is cancelled or one child raises.
                        for task in tasks:
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                    if input_task in done:
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
