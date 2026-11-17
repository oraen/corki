"""Bounded child ownership. No tasks survive their owning runtime's shutdown.

Like Codex agent/control, creation reserves a slot before awaiting startup,
waits observe terminal state, and cancellation does not manufacture completion.
"""

import asyncio
from collections import deque
from contextlib import aclosing, nullcontext
from dataclasses import dataclass, field
from uuid import uuid4

from corki.models.request_watch import request_observer
from corki.protocol.events import TurnCancelled, TurnCompleted, TurnFailed, UserInputRequested
from corki.realtime.controller import RealtimeInputLimitError, RealtimeTurnClosedError


@dataclass
class Child:
    id: str
    status: str = "starting"
    result: str = ""
    context: object = None
    runtime: object = None
    task: asyncio.Task | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    pending: deque[str] = field(default_factory=deque)
    admission: asyncio.Lock = field(default_factory=asyncio.Lock)
    stopping: bool = False
    closed: bool = False
    restored: bool = False
    result_loaded: bool = False
    lifecycle: asyncio.Lock = field(default_factory=asyncio.Lock)

    def snapshot(self):
        return {
            "id": self.id,
            "status": self.status,
            "loaded": self.runtime is not None,
            "result": self.result,
            "pending_messages": len(self.pending),
        }


class AgentController:
    def __init__(self, factory, *, limit=6, store=None):
        self.factory = factory
        self.store = store
        self.membership = asyncio.Lock()
        self._close_task = None
        self._restored = False
        self.limit = limit
        self.children = {}
        self.closed = False
        self.stopping = False

    async def restore(self):
        if self.store is None or self._restored:
            return
        async with self.membership:
            if self._restored:
                return
            for row in await self.store.restore():
                status = row["status"]
                if status in {"starting", "running"}:
                    status = "interrupted"
                child = Child(row["child_id"], status=status, restored=True)
                child.done.set()  # Identity only: never replay work at restoration.
                self.children.setdefault(child.id, child)
            self._restored = True

    async def inspect(self, identity):
        child = self.get(identity)
        if child.restored and not child.result_loaded and self.store is not None:
            row = await self.store.result(identity)
            if row and row["status"] in {"completed", "failed", "cancelled"}:
                child.status = row["status"]
                child.result = row["final_answer"] or row["error"] or ""
            elif child.status in {"interrupted", "suspended"}:
                child.result = "Runtime unloaded; prior work was not automatically resumed."
            child.result_loaded = True
        return child

    async def list(self):
        children = [await self.inspect(i) for i in tuple(self.children)]
        return {"agents": [child.snapshot() for child in children]}

    def _check_capacity(self):
        # Unloaded identities consume no runtime slot. A starting factory does.
        if sum(not child.restored for child in self.children.values()) >= self.limit:
            raise ValueError("Child agent limit reached; close an unused child first")

    def get(self, identity):
        if identity not in self.children:
            raise ValueError(
                "Unknown child agent for this parent. Use list_agents or spawn_agent; "
                "only this parent's registered children are accessible"
            )
        return self.children[identity]

    async def spawn(self, message, context):
        async with self.membership:
            if self.closed or self.stopping:
                raise ValueError("Agent controller is closed")
            self._check_capacity()
            child = Child(str(uuid4()), context=context)
            if self.store is not None:
                try:
                    await self.store.create(child.id)
                except asyncio.CancelledError:
                    # Join the identity write before publishing its rollback tombstone.
                    await self.store.update(child.id, "cancelled", close=True)
                    raise
            if self.closed or self.stopping:
                if self.store is not None:
                    await self.store.update(child.id, "cancelled", close=True)
                raise ValueError("Agent controller is closed")
            # Publish before starting work; durable failure cannot start an orphan.
            self.children[child.id] = child
            child.task = asyncio.create_task(self._run(child, message))
            return child.snapshot()

    async def _run(self, child, message):
        token = request_observer.set(None)
        child.done.clear()
        try:
            if child.runtime is None:
                child.runtime = await self.factory(child.id, child.context)
            while True:
                child.status = "running"
                saturated = False
                async with aclosing(child.runtime.stream(message, realtime=True)) as events:
                    async for event in events:
                        if child.pending and not saturated:
                            saturated = await self._deliver_pending(child)
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
                    child.status = "failed"
                    child.result = "Child stream ended without a terminal event"
                async with child.admission:
                    if not child.pending or child.stopping or child.closed or self.closed:
                        break
                    message = child.pending.popleft()
                    child.result = ""
        except asyncio.CancelledError:
            child.status = "cancelled"
            raise
        except Exception as exc:
            child.status, child.result = "failed", str(exc)
        finally:
            try:
                if self.store is not None:
                    try:
                        await self.store.update(child.id, child.status)
                    except Exception as exc:
                        child.status = "failed"
                        child.result += f"\nChild lifecycle status could not be persisted: {exc}"
            finally:
                child.done.set()
                request_observer.reset(token)

    async def _deliver_pending(self, child):
        """Drain already admitted startup messages at runtime event boundaries."""
        async with child.admission:
            if child.stopping or child.closed:
                return False
            while child.pending:
                try:
                    await child.runtime.steer(child.pending[0])
                except RealtimeTurnClosedError:
                    return False  # The worker starts these after the current turn joins.
                except RealtimeInputLimitError:
                    return True  # Preserve accepted input; no busy retries on every token.
                except Exception:
                    # Delivery may already have happened. Never replay this input on a
                    # later explicit send; surface failure instead of guessing its outcome.
                    child.pending.popleft()
                    raise
                child.pending.popleft()
        return False

    async def wait(self, identities, timeout_ms):
        children = [await self.inspect(identity) for identity in identities]
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

    async def send(self, identity, message, context=None):
        child = self.get(identity)
        async with child.admission:
            if self.closed or self.stopping or child.stopping or child.closed:
                raise ValueError("Child agent is stopping or closed")
            if child.task and not child.task.done():
                # Preserve FIFO and use a bounded handoff only while startup or finalization
                # prevents steering. Other errors must never be mistaken for non-delivery.
                if not child.pending and child.runtime is not None and child.status == "running":
                    try:
                        await child.runtime.steer(message)
                        return child.snapshot()
                    except RealtimeTurnClosedError:
                        pass
                if len(child.pending) >= 64:
                    raise ValueError(
                        "Child message handoff is full; wait before sending more input"
                    )
                child.pending.append(message)
            else:
                if child.restored:
                    if context is None:
                        raise ValueError(
                            "Restoring a child requires the current parent's tool context"
                        )
                    # Current host-owned authority, not instructions recovered from model text.
                    async with self.membership:
                        self._check_capacity()
                        if self.store is not None:
                            await self.store.admit(identity)
                        child.context = context
                        child.restored = False
                elif self.store is not None:
                    await self.store.admit(identity)
                if child.pending:
                    if len(child.pending) >= 64:
                        raise ValueError(
                            "Child message handoff is full; wait before sending more input"
                        )
                    child.pending.append(message)
                    message = child.pending.popleft()
                child.result = ""
                child.status = "starting"
                child.done.clear()
                child.task = asyncio.create_task(self._run(child, message))
            return child.snapshot()

    async def _stop_child(self, child):
        async with child.admission:
            child.stopping = True
            child.pending.clear()
        # Keep stopping set if cleanup fails: follow-up must not race retained resources.
        if child.task and not child.task.done():
            try:
                if child.runtime is not None:
                    await child.runtime.cancel_active()
            finally:
                child.task.cancel()
                await asyncio.gather(child.task, return_exceptions=True)
                child.status = "cancelled"
                child.done.set()
        if child.runtime is not None:
            await child.runtime.aclose()
            child.runtime = None
        if self.store is not None:
            await self.store.update(child.id, child.status)
        child.stopping = False
        return child.snapshot()

    async def _lifecycle(self, identity, *, close):
        child = self.children.get(identity)
        if child is None:
            if close and self.store is not None:
                record = await self.store.lookup(identity)
                if record and record["state"] == "closed":
                    return {"id": identity, "status": "closed"}
            self.get(identity)  # Unknown/foreign IDs still fail closed.
        async with child.lifecycle:
            if close:
                child.closed = True
            lease = (
                self.store.unloaded_lease(identity)
                if close and child.restored and self.store is not None
                else nullcontext()
            )
            async with lease:
                if close and self.store is not None:
                    await self.store.update(identity, child.status, close=True)
                result = await self._stop_child(child)
                if close:
                    self.children.pop(identity, None)
                    return {"id": identity, "status": "closed"}
                return result

    async def _joined_lifecycle(self, identity, *, close):
        task = asyncio.create_task(self._lifecycle(identity, close=close))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Cancellation of a tool waiter must not abandon owned execution cleanup.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            task.result()
            raise

    async def stop(self, identity):
        return await self._joined_lifecycle(identity, close=False)

    async def close(self, identity):
        return await self._joined_lifecycle(identity, close=True)

    async def stop_all(self):
        self.stopping = True
        try:
            async with self.membership:
                residents = [i for i, child in self.children.items() if not child.restored]
            results = await asyncio.gather(
                *(self.stop(i) for i in residents), return_exceptions=True
            )
            for result in results:
                if isinstance(result, BaseException):
                    raise result
        finally:
            self.stopping = False

    async def _shutdown(self):
        if self.store is None:
            results = await asyncio.gather(
                *(self.close(i) for i in tuple(self.children)), return_exceptions=True
            )
        else:
            # Host shutdown unloads runtimes; only close_agent closes durable membership.
            async with self.membership:
                residents = [i for i, child in self.children.items() if not child.restored]
            results = await asyncio.gather(
                *(self.stop(i) for i in residents), return_exceptions=True
            )
        for result in results:
            if isinstance(result, BaseException):
                raise result
        self.children.clear()

    async def aclose(self):
        self.closed = True
        if self._close_task is None or (
            self._close_task.done() and (
                self._close_task.cancelled() or self._close_task.exception() is not None
            )
        ):
            self._close_task = asyncio.create_task(self._shutdown())
        await asyncio.shield(self._close_task)
