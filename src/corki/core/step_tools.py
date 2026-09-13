"""Process-local ownership for a sampling step; handler objects never enter checkpoints."""

import asyncio
from uuid import uuid4

from corki.tools.registry import ToolRegistry, ToolRegistrySnapshot


class StepToolState:
    """Keep only the latest binding; running calls independently retain their snapshot."""

    def __init__(self, *, capture_resources=None) -> None:
        self._key: str | None = None
        self._snapshot: ToolRegistrySnapshot | None = None
        self._capture_resources = capture_resources
        self.resources = None
        self._closing: dict[asyncio.Future, object] = {}
        self._close_error: BaseException | None = None

    def _capture(self) -> None:
        resources = self._capture_resources() if self._capture_resources is not None else None
        previous, self.resources = self.resources, resources
        if resources is not None:
            self._closing[resources.closed] = resources
            resources.closed.add_done_callback(self._closed)
        if previous is not None:
            previous.release()

    def _closed(self, future) -> None:
        self._closing.pop(future, None)
        try:
            future.result()
        except BaseException as exc:
            self._close_error = self._close_error or exc

    async def aclose(self, *, wait_for_calls: bool = True) -> None:
        resources, self.resources = self.resources, None
        if resources is not None:
            resources.release()
        # Successful Turns can leave admitted cells alive for a later Turn.
        # Their leases and manager-owned generations outlive this Step owner.
        pending = tuple(
            future
            for future, binding in self._closing.items()
            if wait_for_calls or binding.released
        )
        if pending:
            joined = asyncio.gather(*pending, return_exceptions=True)
            interrupted = False
            while not joined.done():
                try:
                    await asyncio.shield(joined)
                except asyncio.CancelledError:
                    interrupted = True
            for future in pending:
                self._closed(future)
            if interrupted:
                raise asyncio.CancelledError
        if self._close_error is not None:
            raise self._close_error

    def bind(self, snapshot: ToolRegistrySnapshot) -> str:
        self._capture()
        self._key, self._snapshot = str(uuid4()), snapshot
        return self._key

    def resolve(
        self, key: str | None, registry: ToolRegistry, *, factory=None
    ) -> ToolRegistrySnapshot:
        """Cold recovery rebinds current handlers, still subject to saved-spec validation.

        Missing keys support old checkpoints and direct graph-node callers. A
        recorded call outcome remains authoritative; this does not replay it.
        """
        if self._snapshot is None or self._key != key:
            snapshot = factory() if factory is not None else registry.snapshot()
            self._capture()
            self._key, self._snapshot = (
                key,
                snapshot,
            )
        return self._snapshot
