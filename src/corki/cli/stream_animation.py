"""Display pacing owned by the Runtime event consumer, not a detached writer."""

import asyncio
from time import monotonic

TICK = 0.008333334


class ChunkingPolicy:
    def __init__(self):
        self.catch_up = False
        self.below_since = None
        self.exited_at = None

    def decide(self, depth, age, now, *, catch_up_only=False):
        if not depth:
            if self.catch_up:
                self.exited_at = now
            self.catch_up = False
            self.below_since = None
            return 0
        if not self.catch_up:
            pressure = depth >= 8 or (age is not None and age >= 0.120)
            severe = depth >= 64 or (age is not None and age >= 0.300)
            held = self.exited_at is not None and now - self.exited_at < 0.250
            if pressure and (not held or severe):
                self.catch_up = True
                self.below_since = self.exited_at = None
        elif depth <= 2 and age is not None and age <= 0.040:
            if self.below_since is None:
                self.below_since = now
            elif now - self.below_since >= 0.250:
                self.catch_up = False
                self.below_since = None
                self.exited_at = now
        else:
            self.below_since = None
        return depth if self.catch_up else 0 if catch_up_only else 1


async def animated_events(events, ui):
    iterator = aiter(events)
    pending = None
    owner = None
    deadline = None
    try:
        while True:
            current = ui.stream_animation_owner()
            now = monotonic()
            if current is not owner:
                owner = current
                deadline = now + TICK if owner is not None else None
            if deadline is not None and now >= deadline:
                await ui.commit_stream_tick()
                deadline = monotonic() + TICK
                continue
            if pending is None:
                pending = asyncio.create_task(anext(iterator), name="corki-display-next-event")
            done, _ = await asyncio.wait(
                {pending}, timeout=None if deadline is None else max(0, deadline - now)
            )
            if not done:
                continue
            completed, pending = pending, None
            try:
                event = completed.result()
            except StopAsyncIteration:
                return
            yield event
    finally:
        if pending is not None:
            if not pending.done():
                pending.cancel()
            cleanup = asyncio.gather(pending, return_exceptions=True)
            interrupted = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    interrupted = True
            if interrupted:
                raise asyncio.CancelledError
