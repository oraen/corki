"""Demand-driven pipe reader for Windows handles unsupported by add_reader.

One daemon worker per transport, no prefetch and no default-executor join at
shutdown. Closing the parent pipe releases a blocked read; a failed output pipe
must also allow the bridge process to exit while its input remains open.
"""

import asyncio
import queue
import threading
from contextlib import suppress


class ThreadPipeReader:
    def __init__(self, stream, limit):
        self.stream = stream
        self.limit = limit
        self.requests = queue.Queue(maxsize=1)
        self.closed = False
        self.pending = None
        self.thread = threading.Thread(target=self._run, name="corki-stdio-reader", daemon=True)
        self.thread.start()

    async def readline(self):
        if self.closed:
            return b""
        if self.pending is not None:
            raise RuntimeError("Concurrent pipe reads are not supported")
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.pending = future
        self.requests.put_nowait((loop, future))
        try:
            return await future
        finally:
            # A cancelled blocking OS read cannot safely be replaced by another
            # read against the same stream. Cancellation retires the reader.
            if future.cancelled():
                self.close()
            self.pending = None

    def close(self):
        self.closed = True
        if self.pending is not None:
            self.pending.cancel()
        # Worker observes closed before touching a queued request.
        with suppress(queue.Full):
            self.requests.put_nowait(None)

    def _run(self):
        while not self.closed:
            request = self.requests.get()
            if request is None or self.closed:
                return
            loop, future = request
            try:
                value, error = self.stream.readline(self.limit), None
            except Exception as exc:
                value, error = None, exc
            if self.closed:
                return
            try:
                loop.call_soon_threadsafe(self._deliver, future, value, error)
            except RuntimeError:
                return  # Event loop already shut down.

    @staticmethod
    def _deliver(future, value, error):
        if future.done():
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(value)
