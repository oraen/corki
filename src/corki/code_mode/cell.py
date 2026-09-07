"""Owned engine process, callback tasks and one incremental observer per cell."""

import asyncio
import json
import os
import sys
import time
from contextlib import suppress
from copy import deepcopy
from pathlib import Path

from corki.code_mode.observation import CellObservation
from corki.code_mode.output import truncate_cell_output
from corki.protocol.audio import wav_duration_seconds
from corki.protocol.tools import (
    AudioAttachment,
    EncryptedContent,
    TextContent,
    content_from_payload,
)
from corki.tools.errors import CodeModeToolError, FatalToolError

MAX_BUFFER = 4_000_000


class Cell:
    def __init__(self, service, cell_id, call_id, source, definitions):
        self.service, self.id, self.call_id = service, cell_id, call_id
        self.source, self.definitions = source, definitions
        self.stored_snapshot = deepcopy(service.stored)
        self.output = []
        self.output_chars = 0
        self.omitted = False
        self.status = "running"
        self.error = None
        self.process = None
        self.ready = asyncio.Event()
        self.changed = asyncio.Event()
        self.observing = False
        self.tool_tasks = set()
        self.notifications = set()
        self.notification_chars = 0
        self.seen_calls = set()
        self.task = asyncio.create_task(self._run(), name=f"corki-cell-{cell_id}")

    async def _run(self):
        try:
            spawn = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    sys.executable,
                    "-I",
                    "-u",
                    str(Path(__file__).with_name("worker.py")),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    limit=MAX_BUFFER + 1,
                    env={
                        key: value
                        for key, value in os.environ.items()
                        if key in {"SYSTEMROOT", "WINDIR", "LANG"}
                    },
                )
            )
            try:
                self.process = await asyncio.shield(spawn)
            except asyncio.CancelledError:
                self.process = await spawn
                raise
            await self.send(
                {
                    "source": self.source,
                    "stored": self.stored_snapshot,
                    "memory_bytes": self.service.memory_bytes,
                    "tools": [
                        {"name": name, "description": spec.description, "kind": spec.input_kind}
                        for name, spec in self.definitions.items()
                    ],
                }
            )
            self.ready.set()
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    raise RuntimeError("JavaScript engine exited without a terminal result")
                value = json.loads(line)
                kind = value["type"]
                if kind == "text":
                    text = value["text"]
                    if not isinstance(text, str):
                        raise ValueError("invalid cell text")
                    remaining = MAX_BUFFER - self.output_chars
                    admitted = len(self.output) < 4096
                    if remaining and admitted:
                        self.output.append(TextContent(text[:remaining]))
                        self.output_chars += min(len(text), remaining)
                    self.omitted |= len(text) > remaining or not admitted
                elif kind == "content":
                    part = content_from_payload(value["item"])
                    if isinstance(part, EncryptedContent):
                        raise ValueError("JavaScript cells cannot emit encrypted tool content")
                    if isinstance(part, AudioAttachment):
                        duration = wav_duration_seconds(part.data_url)
                        if duration is not None and duration < 0.025:
                            part = TextContent(
                                "Audio output omitted because the clip is shorter than 25 ms; "
                                "use a longer clip."
                            )
                    size = len(part.text if isinstance(part, TextContent) else part.data_url)
                    if size <= MAX_BUFFER - self.output_chars and len(self.output) < 4096:
                        self.output.append(part)
                        self.output_chars += size
                    else:
                        self.omitted = True
                elif kind == "yield" and self.observing:
                    self.changed.set()
                elif kind == "notify":
                    self.notification_chars += len(value["text"])
                    if len(self.notifications) >= 64 or self.notification_chars > MAX_BUFFER:
                        raise ValueError("cell notification limit exceeded")
                    task = asyncio.create_task(self.service.notify(self.call_id, value["text"]))
                    self.notifications.add(task)
                elif kind == "tool":
                    if len(self.seen_calls) >= self.service.max_calls:
                        raise ValueError("cell nested tool call limit exceeded")
                    if value["id"] in self.seen_calls or value["name"] not in self.definitions:
                        raise ValueError("invalid nested tool identity")
                    self.seen_calls.add(value["id"])
                    task = asyncio.create_task(self._invoke(value))
                    self.tool_tasks.add(task)
                elif kind == "complete":
                    await asyncio.gather(*self.notifications)
                    await self._cancel_tools()
                    if not isinstance(value.get("writes"), dict):
                        raise ValueError("invalid cell store writes")
                    self.service.commit(value["writes"])
                    self.error = value.get("error")
                    self.status = "failed" if self.error is not None else "completed"
                    break
        except asyncio.CancelledError:
            self.status = "terminated"
            raise
        except Exception as error:
            self.error = str(error)
            self.status = "failed"
        finally:
            # Terminate can arrive after the engine has finished but while its
            # callbacks are still unwinding. This owned cleanup must still join.
            cleanup = asyncio.create_task(self._cleanup(), name=f"corki-cell-cleanup-{self.id}")
            cancelled = False
            try:
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        self.status = "terminated"
                        cancelled = True
                error = cleanup.result()
                if error is not None and self.status != "terminated":
                    self.status = "failed"
                    self.error = (
                        f"Code Mode resource cleanup failed: {type(error).__name__}: {error}"
                    )
                    self.service.fail(FatalToolError(self.error))
            finally:
                self.ready.set()
                self.changed.set()
            if cancelled:
                raise asyncio.CancelledError

    async def _cleanup(self):
        error = None
        # Kill before joining callbacks: an infinite JS loop cannot hold up cancellation.
        if self.process is not None:
            try:
                if self.process.returncode is None:
                    self.process.kill()
            except ProcessLookupError:
                pass  # Exited between checking returncode and sending the signal.
            except Exception as exc:
                self.service.record_cleanup_error(exc)
                error = error or exc
            try:
                await self.process.wait()
            except Exception as exc:
                self.service.record_cleanup_error(exc)
                error = error or exc
            try:
                if self.process.stdin is not None:
                    self.process.stdin.close()
            except Exception as exc:
                self.service.record_cleanup_error(exc)
                error = error or exc
        await self._cancel_tools()
        for task in self.notifications:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.notifications, return_exceptions=True)
        return error

    async def _cancel_tools(self):
        for task in self.tool_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tool_tasks, return_exceptions=True)

    async def _invoke(self, value):
        try:
            result = await self.service.invoke(self.definitions[value["name"]], value["input"])
            response = {"type": "response", "id": value["id"], "result": result}
        except CodeModeToolError as error:
            response = {"type": "response", "id": value["id"], "error": str(error)}
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # A broken ledger/event bridge is not a committed tool failure.
            # Report it to the Turn as well as unblocking the cell's promise.
            try:
                message = str(error)
            except Exception:
                message = "exception message unavailable"
            message = f"Code Mode dispatch bridge failed: {type(error).__name__}: {message}"
            self.service.fail(FatalToolError(message))
            response = {"type": "response", "id": value["id"], "error": message}
        with suppress(BrokenPipeError, ConnectionResetError):
            try:
                await self.send(response)
            except ValueError:
                await self.send(
                    {
                        "type": "response",
                        "id": value["id"],
                        "error": "nested tool output exceeds bridge serialization limit",
                    }
                )

    async def send(self, value):
        encoded = (json.dumps(value, ensure_ascii=True, allow_nan=False) + "\n").encode()
        if len(encoded) > MAX_BUFFER:
            raise ValueError("cell bridge serialization limit exceeded")
        self.process.stdin.write(encoded)
        await self.process.stdin.drain()

    async def observe(self, delay_ms, max_tokens):
        if self.observing:
            raise ValueError("cell already has an active observer")
        self.observing = True
        started = time.monotonic()
        try:
            await self.ready.wait()
            if self.status == "running":
                delay = delay_ms / 1000 + (1 if delay_ms >= 10000 else 0)
                with suppress(TimeoutError):
                    await asyncio.wait_for(self.changed.wait(), delay)
            self.changed.clear()
            if self.status != "running":
                await asyncio.shield(asyncio.gather(self.task, return_exceptions=True))
            # Cleanup can fail or be interrupted while this observer joins it.
            # Read the terminal and failure channel only after that join.
            if self.service.failure is not None and self.service.failure.done():
                raise self.service.failure.result()
            status = self.status
            output = list(self.output)
            self.output.clear()
            self.output_chars = 0
            if self.omitted:
                output.append(TextContent("[additional output omitted by cell buffer limit]"))
                self.omitted = False
            header = (
                f"Script running with cell ID {self.id}"
                if status == "running"
                else f"Script {status}"
            )
            if self.error is not None:
                output.append(TextContent(f"Script error:\n{self.error}"))
            if status != "running":
                self.service.cells.pop(self.id, None)
            parts = truncate_cell_output(tuple(output), max_tokens)
            header += f"\nWall time {time.monotonic() - started:.1f} seconds\nOutput:\n"
            return CellObservation((TextContent(header), *parts), status == "failed")
        finally:
            self.observing = False

    async def terminate(self):
        if not self.task.done() and not self.task.cancelling():
            self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
