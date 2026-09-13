"""Cancellation-safe, nonblocking Unix PTY I/O, owned by ProcessManager."""

from __future__ import annotations

import asyncio
import errno
import os


async def _ready(descriptor: int, *, writing: bool = False) -> None:
    loop = asyncio.get_running_loop()
    ready = loop.create_future()

    def notify() -> None:
        if not ready.done():
            ready.set_result(None)

    register = loop.add_writer if writing else loop.add_reader
    unregister = loop.remove_writer if writing else loop.remove_reader
    register(descriptor, notify)
    try:
        await ready
    finally:
        unregister(descriptor)


async def read_pty(descriptor: int) -> bytes:
    while True:
        await _ready(descriptor)
        try:
            return os.read(descriptor, 4096)
        except OSError as exc:
            if exc.errno in (errno.EINTR, errno.EAGAIN, errno.EWOULDBLOCK):
                continue
            if exc.errno == errno.EIO:
                return b""  # Unix PTYs may report closed-slave EOF as EIO.
            raise


async def write_pty(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        try:
            count = os.write(descriptor, remaining)
        except OSError as exc:
            if exc.errno == errno.EINTR:
                continue
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                await _ready(descriptor, writing=True)
                continue
            raise
        if count == 0:
            raise OSError(errno.EIO, "PTY write returned zero bytes")
        remaining = remaining[count:]
