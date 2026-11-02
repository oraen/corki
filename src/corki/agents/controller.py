"""Bounded child ownership. No tasks survive their owning runtime's shutdown.

Like Codex agent/control, creation reserves a slot before awaiting startup,
waits observe terminal state, and cancellation does not manufacture completion.
"""

import asyncio
from contextlib import aclosing
from dataclasses import dataclass, field
from uuid import uuid4

from corki.models.request_watch import request_observer
from corki.protocol.events import TurnCancelled, TurnCompleted, TurnFailed, UserInputRequested


@dataclass
class Child:
    id: str
    status: str = "starting"
    result: str = ""
    context: object = None
    runtime: object = None
    task: asyncio.Task | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)

    def snapshot(self):
        return {"id": self.id, "status": self.status, "result": self.result}


class AgentController:
    def __init__(self, factory, *, limit=6):
        self.factory = factory
        self.limit = limit
        self.children = {}
        self.closed = False
        self.stopping = False

    def get(self, identity):
        if identity not in self.children:
            raise ValueError("Unknown child agent; only this parent's children are accessible")
        return self.children[identity]

    async def spawn(self, message, context):
        if self.closed or self.stopping:
            raise ValueError("Agent controller is closed")
        if len(self.children) >= self.limit:
            raise ValueError("Child agent limit reached; close an unused child first")
        child = Child(str(uuid4()), context=context)
        self.children[child.id] = child
        child.task = asyncio.create_task(self._run(child, message, context))
        return child.snapshot()

    async def _run(self, child, message, context=None):
        token = request_observer.set(None)
        child.done.clear()
        try:
            if child.runtime is None:
                child.runtime = await self.factory(child.id, child.context)
            child.status = "running"
            async with aclosing(child.runtime.stream(message)) as events:
                async for event in events:
                    if isinstance(event, TurnCompleted):
                        child.status, child.result = "completed", event.final_answer
                    elif isinstance(event, TurnFailed):
                        child.status, child.result = "failed", event.error
                    elif isinstance(event, TurnCancelled):
                        child.status = "cancelled"
                    elif isinstance(event, UserInputRequested):
                        child.status = "needs_input"
                        child.result = "Ask the user through the parent agent: " + str(
                            event.questions
                        )
                        await child.runtime.cancel_active()
                        break
            if child.status == "running":
                child.status, child.result = "failed", "Child stream ended without a terminal event"
        except asyncio.CancelledError:
            child.status = "cancelled"
            raise
        except Exception as exc:
            child.status, child.result = "failed", str(exc)
        finally:
            child.done.set()
            request_observer.reset(token)

    async def wait(self, identities, timeout_ms):
        children = [self.get(identity) for identity in identities]
        if not children:
            raise ValueError("At least one child id is required")
        waiters = [asyncio.create_task(child.done.wait()) for child in children]
        try:
            await asyncio.wait(
                waiters, timeout=timeout_ms / 1000, return_when=asyncio.FIRST_COMPLETED
            )
            return {
                "agents": [child.snapshot() for child in children],
                "timed_out": not any(child.done.is_set() for child in children),
            }
        finally:
            for waiter in waiters:
                waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)

    async def send(self, identity, message):
        child = self.get(identity)
        if self.closed or self.stopping:
            raise ValueError("Agent controller is closed")
        if child.task and not child.task.done():
            # Explicit rejection prevents a finalization race from losing a message.
            if child.runtime is None or child.status != "running":
                raise ValueError("Child is starting or finishing; wait, then send again")
            await child.runtime.steer(message)
        else:
            child.result = ""
            child.status = "starting"
            child.done.clear()
            child.task = asyncio.create_task(self._run(child, message))
        return child.snapshot()

    async def stop(self, identity):
        child = self.get(identity)
        if child.task and not child.task.done():
            if child.runtime is not None:
                await child.runtime.cancel_active()
            child.task.cancel()
            await asyncio.gather(child.task, return_exceptions=True)
            child.status = "cancelled"
            child.done.set()
        if child.runtime is not None:
            await child.runtime.aclose()
            child.runtime = None
        return child.snapshot()

    async def close(self, identity):
        child = self.get(identity)
        await self.stop(identity)
        if child.runtime is not None:
            await child.runtime.aclose()
        self.children.pop(identity, None)
        return {"id": identity, "status": "closed"}

    async def stop_all(self):
        self.stopping = True
        try:
            await asyncio.gather(*(self.stop(i) for i in tuple(self.children)))
        finally:
            self.stopping = False

    async def aclose(self):
        self.closed = True
        results = await asyncio.gather(
            *(self.close(i) for i in tuple(self.children)), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
