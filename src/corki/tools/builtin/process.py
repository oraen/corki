"""Lifecycle manager for resumable subprocesses used by shell tools."""

from __future__ import annotations

import asyncio
import os
import signal
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

try:
    import pty
except ImportError:  # pragma: no cover - Windows does not provide pty
    pty = None  # type: ignore[assignment]

_TERMINATE_GRACE_SECONDS = 2.0


@dataclass(slots=True)
class ProcessObservation:
    output: str
    exit_code: int | None
    session_id: str | None
    timed_out: bool = False
    wall_time_seconds: float = 0.0
    chunk_id: str = ""
    original_token_count: int | None = None
    output_omitted_bytes: int = 0


@dataclass(slots=True)
class _ProcessSession:
    id: str
    process: asyncio.subprocess.Process
    output_limit_bytes: int
    head: bytearray = field(default_factory=bytearray)
    chunks: deque[bytes] = field(default_factory=deque)
    buffered_bytes: int = 0
    dropped_bytes: int = 0
    reader_task: asyncio.Task[None] | None = None
    timeout_task: asyncio.Task[None] | None = None
    timed_out: bool = False
    started_at: float = field(default_factory=time.monotonic)
    pty_master_fd: int | None = None

    def append(self, chunk: bytes) -> None:
        head_remaining = max(0, self.output_limit_bytes // 2 - len(self.head))
        self.head.extend(chunk[:head_remaining])
        chunk = chunk[head_remaining:]
        if not chunk:
            return
        tail_limit = self.output_limit_bytes - self.output_limit_bytes // 2
        if len(chunk) >= tail_limit:
            self.dropped_bytes += self.buffered_bytes + len(chunk) - tail_limit
            self.chunks.clear()
            self.chunks.append(chunk[-tail_limit:])
            self.buffered_bytes = tail_limit
            return
        self.chunks.append(chunk)
        self.buffered_bytes += len(chunk)
        while self.buffered_bytes > tail_limit and self.chunks:
            overflow = self.buffered_bytes - tail_limit
            removed = self.chunks.popleft()
            if len(removed) > overflow:
                self.chunks.appendleft(removed[overflow:])
                self.buffered_bytes -= overflow
                self.dropped_bytes += overflow
                break
            self.buffered_bytes -= len(removed)
            self.dropped_bytes += len(removed)

    def take_output(self) -> str:
        marker = (
            f"\n... {self.dropped_bytes} bytes omitted ...\n".encode()
            if self.dropped_bytes
            else b""
        )
        content = (bytes(self.head) + marker + b"".join(self.chunks)).decode(
            "utf-8", errors="replace"
        )
        self.head.clear()
        self.chunks.clear()
        self.buffered_bytes = 0
        self.dropped_bytes = 0
        return content


class ProcessManager:
    """Own child processes independently of any one LangGraph node."""

    def __init__(self, *, output_limit_bytes: int = 1024 * 1024) -> None:
        if type(output_limit_bytes) is not int or output_limit_bytes < 1:
            raise ValueError("output_limit_bytes must be a positive integer")
        self._sessions: dict[str, _ProcessSession] = {}
        self._output_limit_bytes = output_limit_bytes

    async def execute(
        self,
        command: str,
        *,
        cwd: Path,
        yield_seconds: float,
        timeout_seconds: float,
        tty: bool = False,
        login: bool = True,
        max_output_bytes: int | None = None,
    ) -> ProcessObservation:
        master_fd: int | None = None
        if tty and os.name != "nt" and pty is not None:
            master_fd, slave_fd = pty.openpty()
            try:
                try:
                    process = await _spawn(
                        command,
                        cwd=cwd,
                        login=login,
                        stdin=slave_fd,
                        stdout=slave_fd,
                        stderr=slave_fd,
                    )
                except BaseException:
                    os.close(master_fd)
                    master_fd = None
                    raise
            finally:
                os.close(slave_fd)
        else:
            process = await _spawn(
                command,
                cwd=cwd,
                login=login,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        session_id = str(uuid4())
        session = _ProcessSession(
            session_id,
            process,
            max(
                1,
                min(max_output_bytes or self._output_limit_bytes, self._output_limit_bytes),
            ),
            pty_master_fd=master_fd,
        )
        self._sessions[session_id] = session
        session.reader_task = asyncio.create_task(
            self._read_pty_output(session) if master_fd is not None else self._read_output(session)
        )
        session.timeout_task = asyncio.create_task(self._enforce_timeout(session, timeout_seconds))
        started = time.monotonic()
        await self._wait_at_most(process, yield_seconds)
        # A process may have written bytes just before the wait expired while
        # the independent reader task has not yet been scheduled. Give it one
        # event-loop turn so the first observation never spuriously loses
        # already-available output.
        await asyncio.sleep(0)
        return await self._observe(session, started)

    async def write_stdin(
        self,
        session_id: str,
        chars: str,
        *,
        yield_seconds: float,
    ) -> ProcessObservation:
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"unknown or completed process session: {session_id}")
        if session.process.returncode is None and chars:
            if chars == "\x03" and os.name != "nt":
                os.killpg(session.process.pid, signal.SIGINT)
            elif session.pty_master_fd is not None:
                os.write(session.pty_master_fd, chars.encode())
            elif session.process.stdin is not None:
                session.process.stdin.write(chars.encode())
                await session.process.stdin.drain()
        started = time.monotonic()
        await self._wait_at_most(session.process, yield_seconds)
        return await self._observe(session, started)

    async def terminate_all(self) -> None:
        sessions = tuple(self._sessions.values())
        errors: list[BaseException] = []
        for session in sessions:
            try:
                await self._terminate(session.process)
            except BaseException as exc:
                # One uncooperative child must not prevent all other process
                # groups and bookkeeping tasks from being reclaimed.
                errors.append(exc)
        for session in sessions:
            if session.reader_task is not None:
                if session.process.returncode is None:
                    session.reader_task.cancel()
                await asyncio.gather(session.reader_task, return_exceptions=True)
            if session.timeout_task is not None:
                session.timeout_task.cancel()
                await asyncio.gather(session.timeout_task, return_exceptions=True)
            self._close_pty(session)
        self._sessions.clear()
        if errors:
            raise errors[0]

    async def _observe(self, session: _ProcessSession, started: float) -> ProcessObservation:
        running = session.process.returncode is None
        if not running and session.reader_task is not None:
            await session.reader_task
        original_bytes = len(session.head) + session.buffered_bytes + session.dropped_bytes
        metadata = {
            "chunk_id": uuid4().hex[:6],
            "original_token_count": (original_bytes + 3) // 4,
            "output_omitted_bytes": session.dropped_bytes,
            "wall_time_seconds": time.monotonic() - started,
        }
        output = session.take_output()
        if running:
            return ProcessObservation(
                output=output,
                exit_code=None,
                session_id=session.id,
                **metadata,
            )
        if session.timeout_task is not None:
            session.timeout_task.cancel()
            await asyncio.gather(session.timeout_task, return_exceptions=True)
        self._close_pty(session)
        self._sessions.pop(session.id, None)
        return ProcessObservation(
            output=output,
            exit_code=session.process.returncode,
            session_id=None,
            timed_out=session.timed_out,
            **metadata,
        )

    @staticmethod
    async def _wait_at_most(process: asyncio.subprocess.Process, seconds: float) -> None:
        with suppress(TimeoutError):
            await asyncio.wait_for(asyncio.shield(process.wait()), timeout=seconds)

    @staticmethod
    async def _read_output(session: _ProcessSession) -> None:
        assert session.process.stdout is not None
        while chunk := await session.process.stdout.read(4096):
            session.append(chunk)

    @staticmethod
    async def _read_pty_output(session: _ProcessSession) -> None:
        assert session.pty_master_fd is not None
        while True:
            try:
                chunk = await asyncio.to_thread(os.read, session.pty_master_fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            session.append(chunk)

    async def _enforce_timeout(self, session: _ProcessSession, seconds: float) -> None:
        try:
            await asyncio.sleep(seconds)
            if session.process.returncode is None:
                session.timed_out = True
                session.append(f"\nCommand timed out after {seconds:g}s.\n".encode())
                await self._terminate(session.process)
        except asyncio.CancelledError:
            return

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            # The child may have exited between the returncode check and the
            # signal. Waiting still reaps the asyncio transport.
            await process.wait()
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=_TERMINATE_GRACE_SECONDS)
        except (TimeoutError, asyncio.CancelledError) as exc:
            if process.returncode is None:
                try:
                    if os.name != "nt":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
            if isinstance(exc, asyncio.CancelledError):
                raise

    @staticmethod
    def _close_pty(session: _ProcessSession) -> None:
        if session.pty_master_fd is not None:
            with suppress(OSError):
                os.close(session.pty_master_fd)
            session.pty_master_fd = None


async def _spawn(
    command: str,
    *,
    cwd: Path,
    login: bool,
    stdin: object,
    stdout: object,
    stderr: object,
) -> asyncio.subprocess.Process:
    options = {
        "cwd": cwd,
        "stdin": stdin,
        "stdout": stdout,
        "stderr": stderr,
        "start_new_session": os.name != "nt",
    }
    if login:
        shell = os.environ.get("SHELL") or "/bin/sh"
        return await asyncio.create_subprocess_exec(shell, "-lc", command, **options)
    return await asyncio.create_subprocess_shell(command, **options)
