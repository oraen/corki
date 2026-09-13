"""Generation-owned HTTP RPC waiters, independent of individual POST carriers."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from corki.mcp.json_rpc import MCPProtocolError, integer_response_id

logger = logging.getLogger(__name__)


class HttpResponseRouter:
    """Route responses across POST/GET while retaining ownership of pending sends."""

    def __init__(
        self, pending: dict[int, asyncio.Future], inbound: Callable[[dict[str, Any]], bool]
    ) -> None:
        self.pending = pending
        self._inbound = inbound
        self._sends: set[asyncio.Task[None]] = set()
        self._receiver: asyncio.Task[None] | None = None
        self._closed = False

    def deliver(self, message: dict[str, Any]) -> None:
        """Unknown, cancelled and duplicate responses cannot complete another call."""
        if self._inbound(message):
            return
        identity = integer_response_id(message.get("id"))
        future = self.pending.get(identity) if identity is not None else None
        if future is not None and not future.done() and "method" not in message:
            future.set_result(message)

    def start_receiver(self, receive: Callable[[], Awaitable[None]]) -> None:
        """A failed optional GET is not a failed RPC service or a POST retry signal."""
        if self._receiver is not None or self._closed:
            return

        async def run() -> None:
            try:
                await receive()
            except Exception as error:
                logger.warning("MCP common receive stream stopped: %s", type(error).__name__)

        self._receiver = asyncio.create_task(run(), name="mcp-http-common-stream")

    async def exchange(
        self,
        identity: int,
        send: Callable[[], Awaitable[tuple[dict[str, Any] | None, bool]]],
    ) -> dict[str, Any]:
        """Register before sending; Accepted only completes sending, not the RPC."""
        if self._closed:
            raise MCPProtocolError("MCP HTTP response router is closed")
        future = asyncio.get_running_loop().create_future()
        self.pending[identity] = future

        async def run() -> None:
            try:
                message, stream_ended = await send()
                if message is not None:
                    self.deliver(message)
                if stream_ended and not future.done():
                    raise MCPProtocolError("MCP response stream closed before its final response")
            except asyncio.CancelledError:
                future.cancel()
                raise
            except Exception as error:
                if not future.done():
                    future.set_exception(error)
                else:
                    logger.debug("MCP POST ended after RPC completion: %s", type(error).__name__)

        task = asyncio.create_task(run(), name="mcp-http-rpc-send")
        self._sends.add(task)
        task.add_done_callback(self._sends.discard)
        try:
            return await future
        except BaseException:
            task.cancel()
            # Joining owns response cleanup even under repeated caller cancellation.
            joined = asyncio.gather(task, return_exceptions=True)
            while not joined.done():
                try:
                    await asyncio.shield(joined)
                except asyncio.CancelledError:
                    continue
            raise
        finally:
            self.pending.pop(identity, None)

    async def aclose(self) -> None:
        """The generation owner shields this join before deleting the HTTP session."""
        self._closed = True
        tasks = tuple(self._sends) + ((self._receiver,) if self._receiver is not None else ())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for future in tuple(self.pending.values()):
            if not future.done():
                future.set_exception(MCPProtocolError("MCP HTTP transport closed"))
