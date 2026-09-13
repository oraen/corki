"""Step-owned live tasks with ordered parallel/exclusive admission."""

import asyncio
from collections.abc import Awaitable, Callable

from corki.protocol.items import ToolCallItem, ToolResultItem
from corki.tools.execution_gate import ExecutionGate


class LiveTools:
    def __init__(
        self, execute: Callable[[ToolCallItem], Awaitable[ToolResultItem]], *, readiness=None
    ) -> None:
        self._execute = execute
        self._readiness = readiness
        self._gate = ExecutionGate()
        self.tasks: list[asyncio.Task[ToolResultItem]] = []
        self._close_task: asyncio.Task[None] | None = None
        self.failure: asyncio.Future[BaseException] = asyncio.get_running_loop().create_future()

    def submit(self, item: ToolCallItem, *, parallel: bool) -> None:
        async def run() -> ToolResultItem:
            try:
                if self._readiness is not None:
                    await self._readiness(item)
                async with self._gate.enter(parallel=parallel):
                    if self.failure.done():
                        raise self.failure.result()
                    return await self._execute(item)
            except BaseException as error:
                # Poison future admission before a released slot can execute a
                # queued side effect. The owner still cancels and joins siblings.
                if not self.failure.done():
                    self.failure.set_result(error)
                raise

        task = asyncio.create_task(run(), name=f"corki-live-tool-{item.call.id}")
        task.add_done_callback(self._finished)
        self.tasks.append(task)

    def _finished(self, task: asyncio.Task[ToolResultItem]) -> None:
        error = asyncio.CancelledError() if task.cancelled() else task.exception()
        if error is not None and not self.failure.done():
            self.failure.set_result(error)

    async def finish(
        self, *, on_result: Callable[[ToolResultItem], Awaitable[None]] | None = None
    ) -> tuple[ToolResultItem, ...]:
        if on_result is None:
            return tuple(await asyncio.gather(*self.tasks))
        results = []
        for task in self.tasks:
            # Publish the ordered prefix without waiting for the whole batch.
            # A later fatal task must still interrupt an earlier hung handler.
            await asyncio.wait((task, self.failure), return_when=asyncio.FIRST_COMPLETED)
            if self.failure.done():
                raise self.failure.result()
            result = task.result()
            await on_result(result)
            results.append(result)
        return tuple(results)

    async def aclose(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(), name="corki-live-tools-close")
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
        self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _close(self) -> None:
        for task in self.tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
