"""Nonblocking executor body routing with per-stream and shared byte limits."""

import asyncio
from collections import deque
from weakref import WeakValueDictionary

import httpx

from corki.mcp.executor_wire import BodyDelta, ExecutorProtocolError

MAX_QUEUED_FRAMES = 256
MAX_QUEUED_BYTES = 16 * 1024 * 1024


class BodyRouter:
    """A full consumer queue fails that stream instead of blocking RPC responses."""

    def __init__(self) -> None:
        self.routes: WeakValueDictionary[str, ExecutorBody] = WeakValueDictionary()
        self.queued_bytes = 0
        self._next_id = 0

    def next_id(self) -> str:
        self._next_id += 1
        return f"http-{self._next_id}"

    def register(self, identity: str) -> "ExecutorBody":
        if identity in self.routes:
            raise ExecutorProtocolError("executor body stream already registered")
        body = ExecutorBody(self, identity)
        self.routes[identity] = body
        return body

    def receive(self, method: str, params: object) -> None:
        if method != "http/request/bodyDelta":
            return
        delta = BodyDelta.parse(params)
        body = self.routes.get(delta.request_id)
        if body is not None:
            body.deliver(delta)

    def fail_all(self, reason: str) -> None:
        for body in list(self.routes.values()):
            body.fail(reason)


class ExecutorBody(httpx.AsyncByteStream):
    """A response owns its receiver, including queued data after routing ends."""

    def __init__(self, router: BodyRouter, identity: str) -> None:
        self._router, self._identity = router, identity
        self._queue: deque[BodyDelta] = deque()
        self._ready = asyncio.Event()
        self._finished = False
        self._closed = False
        self._failure: str | None = None
        self._next_seq = 1

    def deliver(self, delta: BodyDelta) -> None:
        if len(self._queue) >= MAX_QUEUED_FRAMES:
            self.fail("executor body delta backpressure")
        elif self._router.queued_bytes + delta.size > MAX_QUEUED_BYTES:
            self.fail("executor queued body byte limit exceeded")
        else:
            self._queue.append(delta)
            self._router.queued_bytes += delta.size
            if delta.done or delta.error is not None:
                self._finished = True
                self._router.routes.pop(self._identity, None)
            self._ready.set()

    def fail(self, reason: str) -> None:
        self._failure = reason
        self._finished = True
        self._router.routes.pop(self._identity, None)
        self._ready.set()

    async def __aiter__(self):
        try:
            while not self._closed:
                while not self._queue:
                    if self._finished:
                        if self._failure is not None:
                            raise ExecutorProtocolError(self._failure)
                        return
                    self._ready.clear()
                    await self._ready.wait()
                    if self._closed:
                        return
                delta = self._queue.popleft()
                self._router.queued_bytes -= delta.size
                if delta.seq != self._next_seq:
                    raise ExecutorProtocolError("executor body sequence mismatch")
                self._next_seq += 1
                if delta.error is not None:
                    raise ExecutorProtocolError("executor body failed: " + delta.error[:1000])
                if delta.data:
                    yield delta.data
                if delta.done:
                    return
        finally:
            self._close()

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._router.routes.pop(self._identity, None)
        while self._queue:
            self._router.queued_bytes -= self._queue.popleft().size
        self._ready.set()

    async def aclose(self) -> None:
        self._close()

    def __del__(self) -> None:
        # No task is needed: releasing queued byte permits is synchronous.
        self._close()
