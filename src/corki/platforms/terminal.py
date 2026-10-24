"""Owned startup handshake for the native Windows terminal transport."""

import asyncio
import json
import os
from contextlib import suppress
from pathlib import Path

from corki.platforms.processes import close_job, create_subprocess_exec

START_LIMIT = 8_000_000


async def _discard(process: asyncio.subprocess.Process) -> None:
    close_job(process)
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    process._transport.close()
    await process.wait()


async def spawn_terminal(
    compiler: Path,
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    startup_timeout: float = 30,
) -> asyncio.subprocess.Process:
    """Return only an acknowledged session; cancellation joins and destroys startup.

    The first stdout line is protocol, subsequent bytes are terminal output.
    Native helper diagnostics join that output after readiness, like normal stderr.
    On Windows the helper and its descendants are owned by one outer Job Object.
    """
    if not compiler.is_absolute() or not cwd.is_absolute() or not argv or not argv[0]:
        raise ValueError("terminal requires an absolute compiler, cwd and nonempty command")
    frame = (
        json.dumps(
            {"command": argv, "cwd": str(cwd), "env": dict(os.environ) if env is None else env},
            ensure_ascii=True,
        ).encode()
        + b"\n"
    )
    if len(frame) > START_LIMIT:
        raise ValueError("terminal startup frame exceeds its limit")
    process = None
    spawn = asyncio.create_task(
        create_subprocess_exec(
            str(compiler),
            "--corki-terminal-process",
            cwd=cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    )
    try:
        async with asyncio.timeout(startup_timeout):
            process = await asyncio.shield(spawn)
            process.stdin.write(frame)
            await process.stdin.drain()
            header = await process.stdout.readline()
            try:
                acknowledgment = json.loads(header)
            except (ValueError, UnicodeError):
                # Include bounded native diagnostic text, never the startup frame
                # (which contains the process environment).
                detail = header.decode("utf-8", errors="replace").strip()[:500]
                raise OSError(
                    f"native terminal failed to start: {detail or 'no acknowledgment'}"
                ) from None
            if (
                not isinstance(acknowledgment, dict)
                or set(acknowledgment) != {"ready", "version"}
                or acknowledgment["ready"] is not True
                or type(acknowledgment["version"]) is not int
                or acknowledgment["version"] != 1
            ):
                raise OSError("native terminal startup protocol mismatch")
            return process
    except BaseException:

        async def cleanup():
            try:
                owned = await asyncio.shield(spawn)
            except (Exception, asyncio.CancelledError):
                return
            await _discard(owned)

        owner = asyncio.create_task(cleanup(), name="corki-terminal-startup-cleanup")
        while not owner.done():
            try:
                await asyncio.shield(owner)
            except asyncio.CancelledError:
                continue
        owner.result()
        raise
