"""Bounded best-effort refresh; exact model Steps remain the correctness path."""

import asyncio
import logging
import weakref

from corki.mcp.manager import MCPManager

_LOG = logging.getLogger(__name__)


class MCPPrewarm:
    """Coalesce host invalidations without retaining an idle Runtime or manager."""

    def __init__(self, manager: MCPManager) -> None:
        self._requested = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._closed = False
        self._manager = weakref.ref(manager, self._owner_gone)

    def _owner_gone(self, reference) -> None:
        """Dropping the sender closes native's channel; wake our idle receiver too."""
        task = self._task
        if task is None:
            self._requested.set()
        elif not (loop := task.get_loop()).is_closed():
            loop.call_soon_threadsafe(self._requested.set)

    def request(self) -> None:
        if not self._closed:
            # Event is a one-slot notification, not a queue of obsolete configs.
            self._requested.set()

    def start(self) -> None:
        """Called only after the Runtime's fallible initialization has succeeded."""
        if not self._closed and self._task is None:
            self._task = asyncio.create_task(self._run(), name="corki-mcp-prewarm")

    async def _run(self) -> None:
        while not self._closed:
            await self._requested.wait()
            self._requested.clear()
            if self._closed or (manager := self._manager()) is None:
                return
            try:
                await manager.publish_pending()
            except asyncio.CancelledError:
                raise
            except Exception:
                # The failed claim remains dirty. No hot retry loop; the next
                # host notification or exact Step retries using the latest state.
                _LOG.warning("MCP background reconciliation failed", exc_info=True)
            finally:
                del manager

    async def aclose(self) -> None:
        self._closed = True
        task = self._task
        if task is not None:
            if not task.done() and not task.cancelling():
                task.cancel()
            await asyncio.shield(asyncio.gather(task, return_exceptions=True))
