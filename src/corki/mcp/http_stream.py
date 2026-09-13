"""Ownership of individual HTTP responses, including asynchronous close work."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

logger = logging.getLogger(__name__)


class _OwnedResponseStream(httpx.AsyncByteStream):
    """HTTPX's early is_closed flag must not release ownership of pending cleanup."""

    def __init__(self, stream: httpx.AsyncByteStream) -> None:
        self._stream = stream
        self._close_task: asyncio.Task[None] | None = None

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._stream:
            yield chunk

    async def aclose(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._stream.aclose(), name="mcp-response-close")
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        if cancelled:
            if (
                not self._close_task.cancelled()
                and (error := self._close_task.exception()) is not None
            ):
                logger.warning(
                    "MCP response cleanup failed during cancellation: %s", type(error).__name__
                )
            raise asyncio.CancelledError
        self._close_task.result()


@asynccontextmanager
async def owned_response(response: httpx.Response) -> AsyncIterator[httpx.Response]:
    """Join close on every exit, preserving the primary read/cancellation failure."""
    assert isinstance(response.stream, httpx.AsyncByteStream)
    response.stream = _OwnedResponseStream(response.stream)
    primary_error = None
    try:
        yield response
    except BaseException as error:
        primary_error = error
        raise
    finally:
        try:
            await response.aclose()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if primary_error is None:
                raise
            logger.warning("MCP response cleanup also failed: %s", type(error).__name__)
