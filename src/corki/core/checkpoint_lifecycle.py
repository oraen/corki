"""Close checkpoint ownership without re-entering a consumed context manager."""

import logging
from contextlib import AbstractAsyncContextManager

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

_LOG = logging.getLogger(__name__)


async def close_checkpoint(
    context: AbstractAsyncContextManager[AsyncSqliteSaver],
    checkpointer: AsyncSqliteSaver,
    error: BaseException | None = None,
) -> None:
    try:
        await context.__aexit__(
            type(error) if error is not None else None,
            error,
            error.__traceback__ if error is not None else None,
        )
    except BaseException:
        _LOG.warning("Checkpoint context cleanup failed", exc_info=True)
        try:
            # aiosqlite.close is idempotent after a successful close. Retrying
            # __aexit__ on a consumed async generator would not release it.
            await checkpointer.conn.close()
        except BaseException:
            _LOG.warning("Checkpoint connection fallback cleanup failed", exc_info=True)
        raise
