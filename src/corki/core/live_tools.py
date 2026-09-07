"""Step-owned live tasks with ordered parallel/exclusive admission."""

import asyncio
from collections.abc import Awaitable, Callable

from corki.protocol.items import ToolCallItem, ToolResultItem


class LiveTools:
    def __init__(self, execute: Callable[[ToolCallItem], Awaitable[ToolResultItem]]) -> None:
        self._execute = execute
        self.tasks: list[asyncio.Task[ToolResultItem]] = []
        self._barrier: tuple[asyncio.Task[ToolResultItem], ...] = ()
        self.failure: asyncio.Future[BaseException] = asyncio.get_running_loop().create_future()

    def submit(self, item: ToolCallItem, *, parallel: bool) -> None:
        dependencies = self._barrier if parallel else tuple(self.tasks)

        async def run() -> ToolResultItem:
            for dependency in dependencies:
                await asyncio.shield(dependency)
            return await self._execute(item)

        task = asyncio.create_task(run(), name=f"corki-live-tool-{item.call.id}")
        task.add_done_callback(self._finished)
        self.tasks.append(task)
        if not parallel:
            self._barrier = (task,)

    def _finished(self, task: asyncio.Task[ToolResultItem]) -> None:
        error = asyncio.CancelledError() if task.cancelled() else task.exception()
        if error is not None and not self.failure.done():
            self.failure.set_result(error)

    async def finish(self) -> tuple[ToolResultItem, ...]:
        return tuple(await asyncio.gather(*self.tasks))

    async def aclose(self) -> None:
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
