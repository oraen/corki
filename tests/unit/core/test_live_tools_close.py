"""Closing a Step shares one cleanup owner across all observers."""

import asyncio

import pytest

from corki.core.live_tools import LiveTools
from corki.protocol.items import ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall


@pytest.mark.parametrize("cancellations", [0, 1, 3])
@pytest.mark.parametrize("self_cancel", [False, True])
def test_close_observers_cannot_recancel_tool_cleanup(cancellations, self_cancel):
    async def scenario():
        started, cleaning, release, cleaned = (asyncio.Event() for _ in range(4))

        async def execute(item):
            started.set()
            try:
                if self_cancel:
                    asyncio.current_task().cancel()
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
                cleaned.set()

        live = LiveTools(execute)
        live.submit(
            ToolCallItem(ToolCall("call", "probe", {}), "turn", new_step_id()), parallel=True
        )
        observers = []
        try:
            await started.wait()
            observers.append(asyncio.create_task(live.aclose()))
            await cleaning.wait()
            observers.append(asyncio.create_task(live.aclose()))
            for _ in range(cancellations):
                observers[0].cancel()
                await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert all(not observer.done() for observer in observers)
            assert not live.tasks[0].done()
            assert live.tasks[0].cancelling() == 1
            release.set()
            results = await asyncio.gather(*observers, return_exceptions=True)
            assert (
                isinstance(results[0], asyncio.CancelledError)
                if cancellations
                else results[0] is None
            )
            assert results[1] is None
            assert cleaned.is_set()
            assert live.tasks[0].done()
            owner = live._close_task
            await live.aclose()
            assert live._close_task is owner and owner.done()
        finally:
            release.set()
            await asyncio.gather(*observers, return_exceptions=True)
            await live.aclose()

    asyncio.run(scenario())
