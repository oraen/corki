"""Bounded JSON-lines JSON-RPC transport. No Agent logic belongs here."""

import asyncio
import json
import os
import sys
import uuid
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path

from live import SpooledText
from pipe_reader import ThreadPipeReader

MAX_FRAME = 2 * 1024 * 1024
MAX_MESSAGE = 64 * 1024 * 1024
CHUNK_CHARS = 128000


def encode(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (Path, Enum, SpooledText)):
        return str(value)
    raise TypeError(type(value).__name__)


def dumps(value):
    return json.dumps(
        value, ensure_ascii=False, default=encode, allow_nan=False, separators=(",", ":")
    )


class Transport:
    def __init__(self):
        # Reserve stdout before libraries are loaded; incidental print goes to stderr.
        self.output = sys.stdout
        sys.stdout = sys.stderr
        self.queue = asyncio.Queue(maxsize=16)
        self.disconnected = asyncio.Event()
        self.send_lock = asyncio.Lock()

    async def send(self, message):
        raw = dumps(message)
        size = len(raw.encode())
        if size > MAX_MESSAGE:
            raise ValueError("Response exceeds 64 MB logical message limit")
        # Hold the lock across the whole message so fragments cannot interleave.
        # Backpressure bounds the queue even when a large snapshot is transmitted.
        async with self.send_lock:
            if size <= MAX_FRAME:
                await self._enqueue(raw)
                return
            identity = uuid.uuid4().hex
            for index, offset in enumerate(range(0, len(raw), CHUNK_CHARS)):
                await self._enqueue(
                    dumps(
                        {
                            "jsonrpc": "2.0",
                            "method": "transport.chunk",
                            "params": {
                                "id": identity,
                                "index": index,
                                "bytes": size,
                                "last": offset + CHUNK_CHARS >= len(raw),
                                "text": raw[offset : offset + CHUNK_CHARS],
                            },
                        }
                    )
                )

    async def _enqueue(self, raw):
        if self.disconnected.is_set():
            raise ConnectionError("Desktop transport is closed")
        put = asyncio.create_task(self.queue.put(raw + "\n"))
        closed = asyncio.create_task(self.disconnected.wait())
        try:
            await asyncio.wait((put, closed), return_when=asyncio.FIRST_COMPLETED)
            if self.disconnected.is_set():
                raise ConnectionError("Desktop transport is closed")
        finally:
            put.cancel()
            closed.cancel()
            await asyncio.gather(put, closed, return_exceptions=True)

    async def writer(self):
        while True:
            raw = await self.queue.get()
            try:
                await asyncio.to_thread(self._write, raw)
            finally:
                self.queue.task_done()

    def _write(self, raw):
        self.output.write(raw)
        self.output.flush()

    async def serve(self, service):
        writer = asyncio.create_task(self.writer())
        writer.add_done_callback(lambda _: self.disconnected.set())
        tasks = set()
        reader_transport = None
        thread_reader = None
        # A blocking executor readline cannot be cancelled when the parent closes
        # stdout but keeps stdin open. POSIX pipe readers must belong to the loop.
        if os.name != "nt":
            reader = asyncio.StreamReader(limit=MAX_FRAME)
            reader_transport, _ = await asyncio.get_running_loop().connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer
            )

            async def readline():
                return await reader.readline()
        else:
            thread_reader = ThreadPipeReader(sys.stdin.buffer, MAX_FRAME + 1)
            readline = thread_reader.readline

        async def handle(message):
            identity = message.get("id")
            try:
                if (
                    message.get("jsonrpc") != "2.0"
                    or not isinstance(identity, (str, int))
                    or isinstance(identity, bool)
                ):
                    raise ValueError("Invalid JSON-RPC request")
                params = message.get("params", {})
                if not isinstance(params, dict):
                    raise ValueError("params must be an object")
                result = await service.dispatch(message.get("method"), params)
                await self.send({"jsonrpc": "2.0", "id": identity, "result": result})
            except Exception as error:
                await self.send(
                    {
                        "jsonrpc": "2.0",
                        "id": identity,
                        "error": {"code": -32000, "message": service.safe_error(error)},
                    }
                )

        try:
            while not service.stopping:
                read = asyncio.create_task(readline())
                try:
                    await asyncio.wait((read, writer), return_when=asyncio.FIRST_COMPLETED)
                    if writer.done():
                        self.disconnected.set()
                        writer.result()
                        break
                    raw = read.result()
                finally:
                    read.cancel()
                    await asyncio.gather(read, return_exceptions=True)
                if not raw:
                    break
                if len(raw) > MAX_FRAME or not raw.endswith(b"\n"):
                    raise ValueError("Invalid or oversized protocol frame")
                try:
                    message = json.loads(
                        raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value))
                    )
                    if not isinstance(message, dict):
                        raise ValueError("Expected object")
                except (ValueError, UnicodeError):
                    await self.send(
                        {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32700, "message": "Invalid JSON"},
                        }
                    )
                    continue
                if len(tasks) >= 64:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                task = asyncio.create_task(handle(message))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await service.close()
                drain = asyncio.create_task(self.queue.join())
                try:
                    await asyncio.wait((drain, writer), return_when=asyncio.FIRST_COMPLETED)
                finally:
                    drain.cancel()
                    await asyncio.gather(drain, return_exceptions=True)
            finally:
                self.disconnected.set()
                writer.cancel()
                await asyncio.gather(writer, return_exceptions=True)
                if reader_transport:
                    reader_transport.close()
                if thread_reader:
                    thread_reader.close()
