"""Bounded one-shot subprocess I/O with ownership through startup and cancellation."""

import asyncio
import os
import signal
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path


async def _cleanup(spawn: asyncio.Task) -> None:
    try:
        process = await asyncio.shield(spawn)
    except (Exception, asyncio.CancelledError):
        return
    try:
        if os.name == "posix":
            from corki.tools.builtin.process_groups import signal_owned_group

            with suppress(OSError):
                signal_owned_group(process.pid, signal.SIGKILL)
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        process._transport.close()
        await process.wait()
    finally:
        process._transport.close()


async def run_owned(
    argv: list[str],
    data: bytes,
    *,
    cwd: Path,
    output_limit: int,
    timeout: float = 30.0,
    env: dict[str, str] | None = None,
    accepted_exit_codes: tuple[int, ...] = (0,),
    output_consumer: Callable[[bytes], None] | None = None,
) -> bytes:
    """Join the exact spawned child on every exit; never retry a command."""
    if len(data) > 8_000_000:
        raise ValueError("sandbox helper input exceeds its limit")
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=env,
            start_new_session=os.name == "posix",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        ),
        name="corki-sandbox-helper-spawn",
    )
    writer = None
    try:
        async with asyncio.timeout(timeout):
            try:
                process = await asyncio.shield(spawn)
            except OSError as exc:
                raise ValueError(f"sandbox helper failed to start: {exc}") from exc

            async def write():
                try:
                    process.stdin.write(data)
                    await process.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # The exit status/output determines the actual failure.
                finally:
                    process.stdin.close()

            writer = asyncio.create_task(write(), name="corki-sandbox-helper-input")
            output = bytearray()
            received = 0
            while chunk := await process.stdout.read(min(8192, output_limit + 1 - received)):
                received += len(chunk)
                if received > output_limit:
                    raise ValueError("sandbox helper output exceeds its limit")
                if output_consumer is None:
                    output.extend(chunk)
                else:
                    output_consumer(chunk)
            await writer
            status = await process.wait()
            if status not in accepted_exit_codes:
                raise ValueError(f"sandbox helper exited with status {status}")
            return bytes(output)
    except TimeoutError:
        raise ValueError("sandbox helper timed out; outcome may be unknown") from None
    finally:

        async def cleanup():
            if writer is not None:
                writer.cancel()
            await _cleanup(spawn)
            if writer is not None:
                with suppress(asyncio.CancelledError, BrokenPipeError, ConnectionResetError):
                    await writer

        owner = asyncio.create_task(cleanup(), name="corki-sandbox-helper-cleanup")
        cancelled = False
        while not owner.done():
            try:
                await asyncio.shield(owner)
            except asyncio.CancelledError:
                cancelled = True
        owner.result()
        if cancelled:
            raise asyncio.CancelledError
