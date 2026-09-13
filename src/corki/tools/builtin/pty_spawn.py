"""Thread-safe controlling-terminal startup with an owned exec-status handshake."""

from __future__ import annotations

import asyncio
import errno
import json
import os
import sys
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path

from corki.tools.builtin.process_io import _ready


async def spawn_pty(
    argv: list[str],
    *,
    cwd: Path,
    slave_fd: int,
    terminate: Callable[[asyncio.subprocess.Process], Awaitable[None]],
    env: dict[str, str] | None = None,
) -> asyncio.subprocess.Process:
    read_fd, write_fd = os.pipe()
    process = None
    try:
        os.set_blocking(read_fd, False)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-S",
            str(Path(__file__).with_name("_pty_exec.py")),
            str(write_fd),
            *argv,
            cwd=cwd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            start_new_session=True,
            pass_fds=(write_fd,),
            env=env,
        )
        os.close(write_fd)
        write_fd = -1
        status = bytearray()
        while True:
            await _ready(read_fd)
            try:
                chunk = os.read(read_fd, 4096)
            except (InterruptedError, BlockingIOError):
                continue
            if not chunk:
                break
            status.extend(chunk)
            if len(status) > 4096:
                raise OSError(errno.EIO, "PTY startup status exceeded its bound")
        if status != b"R":
            raw = bytes(status).removeprefix(b"R")
            try:
                error = json.loads(raw)
                code, message = error["errno"], error["message"]
                if type(code) is not int or not isinstance(message, str):
                    raise ValueError("invalid status")
            except (ValueError, KeyError, TypeError):
                code, message = errno.EIO, "PTY helper exited before completing startup"
            raise OSError(code, message)
        return process
    except BaseException:
        if process is not None:

            async def cleanup() -> None:
                try:
                    await terminate(process)
                finally:
                    process._transport.close()

            task = asyncio.create_task(cleanup())
            while not task.done():
                # Join exact ownership, then propagate the original error.
                with suppress(asyncio.CancelledError):
                    await asyncio.shield(task)
            task.result()
        raise
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)
