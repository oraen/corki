"""Bounded, group-owned local header helper execution and strict secret-safe output."""

import asyncio
import json
import os
import signal
from contextlib import suppress
from pathlib import Path

import httpx

from corki.config import MCPServerSettings
from corki.mcp.environment import build_stdio_environment
from corki.mcp.http_headers import _HEADER_NAME, _WHITESPACE, _value_bytes
from corki.tools.builtin.process_groups import signal_owned_group

HELPER_TIMEOUT = 10.0
MAX_OUTPUT = 64 * 1024
RESERVED = frozenset(
    [
        "accept",
        "connection",
        "content-encoding",
        "content-length",
        "content-type",
        "host",
        "keep-alive",
        "last-event-id",
        "mcp-protocol-version",
        "mcp-session-id",
        "origin",
        "proxy-connection",
        "referer",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    ]
)


def parse_output(data: bytes) -> httpx.Headers:
    def entries(pairs):
        result = {}
        for name, value in pairs:
            if not isinstance(value, str):
                raise ValueError("helper must output a JSON object of strings")
            lower = name.lower()
            if lower in result:
                raise ValueError("helper returned duplicate header names")
            if not _HEADER_NAME.fullmatch(name):
                raise ValueError("helper returned an invalid header name")
            if lower in RESERVED:
                raise ValueError("helper returned a reserved header")
            if _value_bytes(value) is None:
                raise ValueError("helper returned an invalid header value")
            result[lower] = value
        return result

    try:
        text = data.decode("utf-8")
    except UnicodeError:
        raise ValueError("helper wrote non-UTF-8 data") from None
    try:
        result = json.loads(text.strip(_WHITESPACE), object_pairs_hook=entries)
    except (json.JSONDecodeError, RecursionError):
        raise ValueError("helper must output a JSON object of strings") from None
    if not isinstance(result, dict):
        raise ValueError("helper must output a JSON object of strings")
    return httpx.Headers(result, encoding="utf-8")


async def _cleanup(spawn: asyncio.Task) -> None:
    try:
        process = await asyncio.shield(spawn)
    except (Exception, asyncio.CancelledError):
        return
    try:
        with suppress(OSError):
            signal_owned_group(process.pid, signal.SIGKILL)
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        process._transport.close()
        await process.wait()
    finally:
        process._transport.close()


async def run_helper(command: str, cwd: Path) -> httpx.Headers:
    if os.name != "posix":
        raise ValueError("HTTP headers helper requires supported process containment")
    env = build_stdio_environment(MCPServerSettings("header-helper", "stdio", command="sh"))
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(
            "sh",
            "-c",
            command,
            cwd=cwd,
            env=env,
            process_group=0,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        ),
        name="mcp-header-helper-spawn",
    )
    try:
        try:
            process = await asyncio.shield(spawn)
        except OSError:
            raise ValueError("HTTP headers helper failed to start") from None
        try:
            async with asyncio.timeout(HELPER_TIMEOUT):
                output = bytearray()
                while chunk := await process.stdout.read(min(8192, MAX_OUTPUT + 1 - len(output))):
                    output.extend(chunk)
                    if len(output) > MAX_OUTPUT:
                        raise ValueError("HTTP headers helper output exceeds 64 KiB")
                status = await process.wait()
                if status != 0:
                    raise ValueError(f"HTTP headers helper exited with status {status}")
        except TimeoutError:
            raise ValueError("HTTP headers helper timed out") from None
        return parse_output(bytes(output))
    finally:
        cleanup = asyncio.create_task(_cleanup(spawn), name="mcp-header-helper-cleanup")
        cancelled = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cancelled = True
        cleanup.result()
        if cancelled:
            raise asyncio.CancelledError
