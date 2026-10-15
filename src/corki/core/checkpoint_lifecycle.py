"""Bootstrap and close checkpoint ownership without replaying graph work."""

import asyncio
import logging
import sqlite3
from contextlib import AbstractAsyncContextManager

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

_LOG = logging.getLogger(__name__)
_SETUP_BUSY_RETRY_SECONDS = 5.0


async def setup_checkpoint(checkpointer: AsyncSqliteSaver) -> None:
    """Retry only idempotent bootstrap when SQLite's busy handler returns early.

    The retry window includes setup calls; each SQLite statement still has the
    driver's timeout. This is not a deadline for a running worker-thread statement.
    Cancellation propagates to the Runtime's owned connection cleanup.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _SETUP_BUSY_RETRY_SECONDS
    while True:
        try:
            await checkpointer.setup()
            return
        except sqlite3.OperationalError as error:
            code = getattr(error, "sqlite_errorcode", None)
            if not isinstance(code, int) or code & 0xFF != sqlite3.SQLITE_BUSY:
                raise
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise
            # Only WAL/schema bootstrap is repeated, never checkpoint writes,
            # Turn admission, sampling or tool execution. Its DDL is IF NOT EXISTS.
            await asyncio.sleep(min(0.01, remaining))
            if loop.time() >= deadline:
                raise


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
