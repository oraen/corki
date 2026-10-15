import asyncio

import pytest

from corki.protocol.ids import new_turn_id
from corki.realtime import RealtimeController, RealtimeInput, RealtimeStop, RealtimeTurnClosedError


def test_realtime_controller_orders_inputs_and_stop() -> None:
    async def scenario() -> tuple[object, object]:
        controller = RealtimeController(2)
        controller.activate(new_turn_id())
        await controller.steer("first")
        await controller.stop()
        return await controller.next(), await controller.next()

    first, second = asyncio.run(scenario())

    assert first == RealtimeInput("first")
    assert isinstance(second, RealtimeStop)


def test_realtime_controller_enforces_per_turn_bound() -> None:
    async def scenario() -> None:
        controller = RealtimeController(1)
        controller.activate(new_turn_id())
        await controller.steer("first")
        with pytest.raises(RuntimeError, match="limit"):
            await controller.steer("second")

    asyncio.run(scenario())


def test_realtime_stop_is_idempotent_even_when_input_capacity_is_full() -> None:
    async def scenario() -> tuple[object, ...]:
        controller = RealtimeController(1)
        controller.activate(new_turn_id())
        await controller.steer("full")
        await controller.stop()
        await controller.stop()
        return controller.take_pending()

    commands = asyncio.run(asyncio.wait_for(scenario(), timeout=0.5))
    assert commands == (RealtimeInput("full"), RealtimeStop())


def test_finish_checks_pending_input_and_closes_acceptance_atomically() -> None:
    async def scenario():
        controller = RealtimeController(2)
        controller.activate(new_turn_id())
        await controller.steer("pending")
        assert not controller.finish_if_idle()
        command = await controller.next()
        assert controller.unrecorded_items == (command.item,)
        controller.acknowledge((command.item,))
        assert controller.finish_if_idle()
        assert controller.active, "closing input is distinct from releasing turn ownership"
        with pytest.raises(RealtimeTurnClosedError):
            await controller.steer("too late")
        await controller.stop()
        assert controller.take_pending() == ()
        controller.deactivate()
        controller.activate(new_turn_id())
        await controller.steer("new turn")

    asyncio.run(scenario())


def test_dequeued_unrecorded_input_is_not_discarded_on_deactivate_or_new_turn() -> None:
    async def scenario():
        controller = RealtimeController(2)
        controller.activate(new_turn_id())
        await controller.steer("preserve")
        command = await controller.next()
        controller.close_input()
        controller.deactivate()
        assert controller.unrecorded_items == (command.item,)
        with pytest.raises(RuntimeError, match="not been recorded"):
            controller.activate(new_turn_id())
        controller.acknowledge((command.item,))
        controller.activate(new_turn_id())
        assert controller.unrecorded_items == ()

    asyncio.run(scenario())


def test_retry_wait_only_observes_stop_and_does_not_consume_pending_text():
    from corki.core.retry import wait_retry

    async def scenario():
        controller = RealtimeController(2)
        controller.activate(new_turn_id())
        task = asyncio.create_task(wait_retry(0.04, controller))
        await controller.steer("pending")
        await asyncio.sleep(0.005)
        assert not task.done()
        assert await asyncio.wait_for(task, 1) is None
        task = asyncio.create_task(wait_retry(60, controller))
        await controller.stop()
        assert isinstance(await asyncio.wait_for(task, 1), RealtimeStop)
        commands = controller.take_pending()
        assert commands == (RealtimeInput("pending"), RealtimeStop())
        controller.acknowledge((commands[0].item,))
        controller.deactivate()
        controller.activate(new_turn_id())
        assert await wait_retry(0, controller) is None

    asyncio.run(scenario())
