import asyncio
from types import SimpleNamespace

import pytest

from corki.cli import stream_animation
from corki.cli.stream_animation import TICK, ChunkingPolicy, animated_events


def test_chunking_age_depth_hysteresis_and_severe_reentry():
    policy = ChunkingPolicy()
    assert policy.decide(7, 0.119, 1.0, catch_up_only=True) == 0
    assert policy.decide(7, 0.119, 1.0) == 1
    assert policy.decide(8, 0, 1.0) == 8
    assert policy.decide(2, 0.040, 1.0) == 2
    assert policy.decide(2, 0.040, 1.249) == 2
    assert policy.decide(2, 0.040, 1.250) == 1
    assert policy.decide(8, 0.120, 1.251) == 1
    assert policy.decide(64, 0.0, 1.252) == 64
    assert policy.decide(0, None, 1.253) == 0
    assert policy.decide(8, 0.120, 1.254) == 1
    assert policy.decide(1, 0.300, 1.255) == 1
    assert policy.catch_up


def test_exit_requires_continuously_low_pressure():
    policy = ChunkingPolicy()
    assert policy.decide(1, 0.120, 1.0) == 1
    assert policy.catch_up
    policy.decide(2, 0.040, 1.1)
    policy.decide(3, 0.040, 1.2)
    policy.decide(2, 0.040, 1.4)
    assert policy.catch_up
    policy.decide(2, 0.040, 1.7)
    assert not policy.catch_up


@pytest.mark.parametrize("transition", ["same", "replace", "stop_restart"])
def test_event_consumer_deadlines_survive_stalls_and_discard_old_owners(monkeypatch, transition):
    asyncio.run(_deadline_scenario(monkeypatch, transition))


async def _deadline_scenario(monkeypatch, transition):
    # Drive the production event wrapper with virtual time, not a second timer
    # implementation. Ready model events consume no time; idle waits advance it.
    now = 0.0
    waits = []
    ticks = []
    released = asyncio.Event()

    async def wait(tasks, *, timeout):
        nonlocal now
        waits.append(timeout)
        await asyncio.sleep(0)
        done = {task for task in tasks if task.done()}
        if done:
            return done, tasks - done
        assert timeout is not None, "event producer unexpectedly blocked without animation"
        assert len(waits) < 10, "animation failed to release the model producer"
        now += timeout
        return set(), tasks

    monkeypatch.setattr(stream_animation, "monotonic", lambda: now)
    # Replace only this module's asyncio facade, not the event loop's wait API.
    monkeypatch.setattr(
        stream_animation,
        "asyncio",
        SimpleNamespace(
            wait=wait,
            create_task=asyncio.create_task,
            gather=asyncio.gather,
            shield=asyncio.shield,
            CancelledError=asyncio.CancelledError,
        ),
    )

    class UI:
        owner = object()

        def stream_animation_owner(self):
            return self.owner

        async def commit_stream_tick(self):
            ticks.append(now)
            if len(ticks) == 2:
                self.owner = None
                released.set()

    async def events():
        yield "first"
        yield "second"
        if transition == "stop_restart":
            yield "restart"
        await released.wait()

    ui = UI()
    received = []
    async for event in animated_events(events(), ui):
        received.append(event)
        if event == "first":
            now += TICK / 2
        elif event == "second":
            now += 5 * TICK  # The display consumer was blocked across five ticks.
            if transition == "replace":
                ui.owner = object()
            elif transition == "stop_restart":
                ui.owner = None
        else:
            ui.owner = object()

    # Repeated data did not restart the first deadline. After a stall, consume
    # only one overdue tick and schedule the next one relative to current time.
    assert waits[:2] == pytest.approx([TICK, TICK / 2])
    first_tick = 5.5 if transition == "same" else 6.5
    assert ticks == pytest.approx([first_tick * TICK, (first_tick + 1) * TICK])
    if transition == "stop_restart":
        assert waits[2] is None
    assert received == ["first", "second"] + (["restart"] if transition == "stop_restart" else [])
    assert not any(task.get_name() == "corki-display-next-event" for task in asyncio.all_tasks())
