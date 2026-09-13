"""Bounded waiting is separate from ownership of an unconfirmed worker close."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Like the source thread manager, retain unconfirmed workers beyond one job's
# lifetime. asyncio itself keeps only weak references to background Tasks.
_RETAINED_OWNERS: set[ConsolidationShutdowns] = set()
_LOG = logging.getLogger(__name__)


def _describe(error: BaseException) -> str:
    try:
        message = str(error)
    except BaseException:
        message = "<message unavailable>"
    text = f"{type(error).__name__}: {message}"
    return text if len(text) <= 4000 else text[:3997] + "..."


class ConsolidationShutdownError(RuntimeError):
    def __init__(self, message: str, *, cancelled: bool = False):
        super().__init__(message)
        self.cancelled = cancelled


@dataclass(frozen=True, slots=True)
class RetainedWorker:
    thread_id: str
    working_copy: Path
    status: str
    error: str | None


@dataclass(slots=True)
class _Worker:
    runtime: Any
    working_copy: Path
    closing: asyncio.Task
    cleanup: Callable[[], Awaitable[None]]
    reclaiming: asyncio.Task | None = None
    status: str = "closing"
    error: str | None = None


class ConsolidationShutdowns:
    def __init__(self):
        self._workers: dict[asyncio.Task, _Worker] = {}
        self.warnings: list[str] = []

    @property
    def retained(self) -> tuple[RetainedWorker, ...]:
        return tuple(
            RetainedWorker(str(w.runtime.thread_id), w.working_copy, w.status, w.error)
            for w in sorted(self._workers.values(), key=lambda w: str(w.working_copy))
        )

    async def close(self, runtime, working_copy, cleanup, *, timeout: float) -> bool:
        closing = asyncio.create_task(runtime.aclose(), name="memory-worker-close")
        deadline = asyncio.get_running_loop().time() + timeout
        cancelled = False
        try:
            while not closing.done():
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError(f"worker shutdown exceeded {timeout:g} seconds")
                try:
                    await asyncio.wait((closing,), timeout=remaining)
                except asyncio.CancelledError:
                    cancelled = True  # Do not cancel teardown or restart its deadline.
            closing.result()
        except BaseException as exc:
            worker = _Worker(runtime, working_copy, closing, cleanup)
            self._workers[closing] = worker
            _RETAINED_OWNERS.add(self)
            if closing.done():
                self._closed(worker)
            else:
                closing.add_done_callback(lambda _: self._closed(worker))
            raise ConsolidationShutdownError(
                f"consolidation shutdown unconfirmed; retained {working_copy}: {_describe(exc)}",
                cancelled=cancelled,
            ) from exc
        return cancelled

    def _closed(self, worker: _Worker) -> None:
        if worker.status != "closing":
            return
        try:
            worker.closing.result()
        except BaseException as exc:
            self._failed(worker, "close", exc)
            return
        worker.status = "reclaiming"
        worker.reclaiming = asyncio.create_task(self._reclaim(worker), name="memory-worker-reclaim")

    async def _reclaim(self, worker: _Worker) -> None:
        # Cleanup owns filesystem work too: event-loop cancellation must not
        # discard a threadpool writer and then report the directory reclaimed.
        cleanup = asyncio.create_task(worker.cleanup())
        try:
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            cleanup.result()
        except BaseException as exc:
            self._failed(worker, "copy cleanup", exc)
            return
        self._workers.pop(worker.closing)
        if not self._workers:
            _RETAINED_OWNERS.discard(self)

    def _failed(self, worker: _Worker, operation: str, error: BaseException) -> None:
        worker.status = "failed"
        worker.error = f"{operation}: {_describe(error)}"
        warning = (
            f"retained memory worker {operation} failed at {worker.working_copy}: {worker.error}"
        )
        self.warnings.append(warning)
        _LOG.warning("%s", warning)

    async def settle(self, *, timeout: float = 10.0) -> tuple[RetainedWorker, ...]:
        """Bound a host's wait; pending/failed workers stay owned and inspectable."""
        deadline = asyncio.get_running_loop().time() + max(0, timeout)
        while True:
            await asyncio.sleep(0)  # Let completed close callbacks schedule copy reclamation.
            # A Task can finish before its scheduled done callback runs. Observe
            # that completion now; _closed is idempotent when the callback follows.
            for worker in tuple(self._workers.values()):
                if worker.closing.done():
                    self._closed(worker)
            pending = tuple(
                task
                for worker in self._workers.values()
                for task in (worker.closing, worker.reclaiming)
                if task is not None and not task.done()
            )
            remaining = deadline - asyncio.get_running_loop().time()
            if not pending or remaining <= 0:
                return self.retained
            await asyncio.wait(pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
