"""Lifecycle manager for resumable subprocesses used by shell tools."""

from __future__ import annotations

import asyncio
import os
import signal
import time
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import uuid4

from corki.config.permissions import ExecutionPermissions
from corki.config.shell_environment import ShellEnvironmentPolicy
from corki.execution.approvals import ExecutionApprovals, SandboxPermissions
from corki.execution.backend import sandbox_command
from corki.execution.retry import SandboxAdmission
from corki.execution.terminal import (
    TerminalSnapshot,
    close_terminal_reviews,
    owned_terminal_review,
    unrestricted_terminal,
)
from corki.protocol.execution_identity import ExecutionIdentity
from corki.protocol.terminals import BackgroundTerminalInfo
from corki.shell import Shell, default_user_shell
from corki.tools.builtin.process_groups import signal_owned_group
from corki.tools.builtin.process_io import read_pty, write_pty
from corki.tools.builtin.process_retention import select_prunable
from corki.tools.builtin.process_retry import finish_sandbox_startup
from corki.tools.builtin.process_status import observation_exit_code
from corki.tools.builtin.pty_spawn import spawn_pty
from corki.tools.builtin.shell_environment import unified_exec_environment

try:
    import pty
except ImportError:  # pragma: no cover - Windows does not provide pty
    pty = None  # type: ignore[assignment]

_TERMINATE_GRACE_SECONDS = 2.0
_POST_EXIT_DRAIN_SECONDS = 0.05


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
    terminal_info: BackgroundTerminalInfo | None = None


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
    writer_task: asyncio.Task[None] | None = None
    input_queue: asyncio.Queue[bytes] = field(default_factory=lambda: asyncio.Queue(maxsize=128))
    timeout_task: asyncio.Task[None] | None = None
    cleanup_task: asyncio.Task[None] | None = None
    termination_task: asyncio.Task[None] | None = None
    failure: BaseException | None = None
    failure_event: asyncio.Event = field(default_factory=asyncio.Event)
    timed_out: bool = False
    started_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)
    interaction_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pty_master_fd: int | None = None
    tty: bool = False
    terminal_info: BackgroundTerminalInfo | None = None
    permissions: TerminalSnapshot | None = None
    permission_compiler: Path | None = None

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

    def peek_output(self) -> str:
        marker = (
            f"\n... {self.dropped_bytes} bytes omitted ...\n".encode()
            if self.dropped_bytes
            else b""
        )
        return (bytes(self.head) + marker + b"".join(self.chunks)).decode("utf-8", errors="replace")

    def take_output(self) -> str:
        content = self.peek_output()
        self.head.clear()
        self.chunks.clear()
        self.buffered_bytes = 0
        self.dropped_bytes = 0
        return content


class ProcessManager:
    """Own child processes independently of any one LangGraph node."""

    def __init__(
        self,
        *,
        output_limit_bytes: int = 1024 * 1024,
        shell: Shell | None = None,
        environment_policy: ShellEnvironmentPolicy | None = None,
    ) -> None:
        if type(output_limit_bytes) is not int or output_limit_bytes < 1:
            raise ValueError("output_limit_bytes must be a positive integer")
        self._sessions: dict[str, _ProcessSession] = {}
        self._shell = shell or default_user_shell()
        self._environment_policy = environment_policy or ShellEnvironmentPolicy()
        self._identity: ExecutionIdentity | None = None
        self._identity_resolver: Callable[[], Awaitable[None]] | None = None
        self._admitted = False
        self._output_limit_bytes = output_limit_bytes
        self._starting: dict[asyncio.Task[_ProcessSession], asyncio.Event] = {}
        self._generation = 0
        self._closing = 0
        self._stdin_reviews: set[asyncio.Task] = set()
        self.approvals = ExecutionApprovals()

    @property
    def shell(self) -> Shell:
        """The immutable Session default; per-call overrides never replace it."""
        return self._shell

    def bind_identity(self, identity: ExecutionIdentity) -> None:
        """Bind only before process admission; an owned manager cannot change identity."""
        if not isinstance(identity, ExecutionIdentity):
            raise TypeError("identity must be an ExecutionIdentity")
        if self._identity == identity:
            return
        if self._identity is not None or self._admitted:
            raise RuntimeError("cannot change process manager execution identity")
        self._identity = identity

    def set_identity_resolver(self, resolver: Callable[[], Awaitable[None]]) -> None:
        """Attach host metadata initialization before exposing a Runtime-owned manager."""
        if (
            self._identity_resolver is not None
            or self._identity is not None
            or self._admitted
            or self._starting
        ):
            raise RuntimeError("cannot change process manager identity resolver")
        self._identity_resolver = resolver

    async def execute(
        self,
        command: str,
        *,
        cwd: Path,
        yield_seconds: float,
        timeout_seconds: float | None = None,
        tty: bool = False,
        login: bool = True,
        max_output_bytes: int | None = None,
        item_id: str = "",
        shell: Shell | None = None,
        permissions: ExecutionPermissions | None = None,
        sandbox_permissions: SandboxPermissions = "use_default",
        justification: str | None = None,
        prefix_rule: list[str] | None = None,
        honor_allow_prefix_rules: bool = True,
        on_warning: Callable[[str], Awaitable[None]] | None = None,
        write_stdin_approval: bool = False,
        terminal_policy_cwd: Path | None = None,
    ) -> ProcessObservation:
        if self._closing:
            raise asyncio.CancelledError
        generation = self._generation
        admission_cancelled = asyncio.Event()
        startup = asyncio.create_task(
            self._resolve_and_start(
                command,
                cwd,
                timeout_seconds,
                tty,
                login,
                max_output_bytes,
                item_id=item_id,
                shell=shell or self.shell,
                inherited=dict(os.environ),
                permissions=permissions,
                generation=generation,
                admission_cancelled=admission_cancelled,
                sandbox_permissions=sandbox_permissions,
                justification=justification,
                prefix_rule=prefix_rule,
                honor_allow_prefix_rules=honor_allow_prefix_rules,
                on_warning=on_warning,
                write_stdin_approval=write_stdin_approval,
                terminal_policy_cwd=terminal_policy_cwd or cwd,
            ),
            name="corki-process-startup",
        )
        self._starting[startup] = admission_cancelled
        cancelled = False
        try:
            while not startup.done():
                try:
                    await asyncio.shield(startup)
                except asyncio.CancelledError:
                    cancelled = True
                    admission_cancelled.set()
            session = startup.result()
        except Exception as exc:
            if cancelled:
                raise asyncio.CancelledError from exc
            raise
        finally:
            self._starting.pop(startup, None)
        if cancelled or generation != self._generation:
            await self._retire(session)
            raise asyncio.CancelledError
        # Ownership has transferred. Interrupting the observation alone must not
        # kill a stored background process (Codex's initial-exec store boundary).
        started = time.monotonic()
        await self._wait_session(session, yield_seconds)
        await asyncio.sleep(0)
        return await self._observe(session, started)

    async def _resolve_and_start(
        self,
        command: str,
        cwd: Path,
        timeout_seconds: float | None,
        tty: bool,
        login: bool,
        max_output_bytes: int | None,
        *,
        item_id: str,
        shell: Shell,
        inherited: dict[str, str],
        permissions: ExecutionPermissions | None,
        generation: int,
        admission_cancelled: asyncio.Event,
        sandbox_permissions: SandboxPermissions,
        justification: str | None,
        prefix_rule: list[str] | None,
        honor_allow_prefix_rules: bool,
        on_warning: Callable[[str], Awaitable[None]] | None,
        write_stdin_approval: bool,
        terminal_policy_cwd: Path,
    ) -> _ProcessSession:
        if sandbox_permissions not in {"use_default", "require_escalated"}:
            raise ValueError("unsupported sandbox_permissions")
        if permissions is None and sandbox_permissions != "use_default":
            raise ValueError("model escalation requires a configured execution permission backend")
        if self._identity_resolver is not None:
            await self._identity_resolver()
            if self._identity is None:
                raise RuntimeError("host did not resolve process execution identity")
        if self._closing or generation != self._generation or admission_cancelled.is_set():
            raise asyncio.CancelledError
        prepared_argv = None
        admission = SandboxAdmission(require_terminal_snapshot=write_stdin_approval)
        if permissions is not None:
            review = (
                {"approvals": self.approvals, "call_id": item_id, "tty": tty}
                if permissions.approval_policy_json != '"never"'
                else {}
            )
            if sandbox_permissions != "use_default" or justification is not None:
                review.update(sandbox_permissions=sandbox_permissions, justification=justification)
            if prefix_rule is not None or not honor_allow_prefix_rules:
                review.update(
                    prefix_rule=prefix_rule, honor_allow_prefix_rules=honor_allow_prefix_rules
                )
            if permissions.approval_policy_json != '"never"' and on_warning is not None:
                review["on_warning"] = on_warning
            preparation = asyncio.create_task(
                sandbox_command(
                    permissions,
                    shell.exec_args(command, login=login),
                    cwd,
                    admission=admission,
                    **review,
                ),
                name="corki-process-preflight",
            )
            cancelled = asyncio.create_task(admission_cancelled.wait())
            try:
                await asyncio.wait((preparation, cancelled), return_when=asyncio.FIRST_COMPLETED)
                if admission_cancelled.is_set():
                    raise asyncio.CancelledError
                prepared_argv = preparation.result()
            finally:
                for task in (preparation, cancelled):
                    if not task.done():
                        task.cancel()
                # The startup owner is shielded by execute/terminate_all. Join
                # preparation (including its helper cleanup) before leaving it;
                # do not extend this cancellation boundary across process spawn.
                await asyncio.gather(preparation, cancelled, return_exceptions=True)
                if admission_cancelled.is_set() and not preparation.cancelled():
                    error = preparation.exception()
                    if error is not None:
                        raise asyncio.CancelledError from error
            # Policy preparation can yield. Cancellation/close must be rechecked
            # before admitting any model-command side effects.
            if self._closing or generation != self._generation or admission_cancelled.is_set():
                raise asyncio.CancelledError
        self._admitted = True
        environment = unified_exec_environment(
            inherited, self._environment_policy, identity=self._identity
        )

        async def spawn(retry_argv):
            session = await self._start_session(
                command,
                cwd,
                timeout_seconds,
                tty,
                login,
                max_output_bytes,
                item_id=item_id,
                shell=shell,
                prepared_argv=prepared_argv if retry_argv is None else retry_argv,
                env=environment,
                publish=admission.plan is None,
            )
            snapshot = (
                admission.terminal_snapshot
                if permissions is not None
                else unrestricted_terminal(terminal_policy_cwd)
            )
            session.permissions = (
                replace(snapshot, bypassed=True)
                if retry_argv is not None and snapshot is not None
                else snapshot
            )
            session.permission_compiler = permissions.compiler if permissions is not None else None
            return session

        if admission.plan is not None:
            return await finish_sandbox_startup(
                self,
                spawn,
                admission,
                permissions,
                cwd,
                call_id=item_id,
                tty=tty,
                cancelled=admission_cancelled,
                generation=generation,
            )
        return await spawn(None)

    async def _start_session(
        self,
        command: str,
        cwd: Path,
        timeout_seconds: float | None,
        tty: bool,
        login: bool,
        max_output_bytes: int | None,
        *,
        item_id: str = "",
        shell: Shell | None = None,
        env: dict[str, str] | None = None,
        prepared_argv: list[str] | None = None,
        publish: bool = True,
    ) -> _ProcessSession:
        session_id = str(uuid4())
        output_limit = max(
            1, min(max_output_bytes or self._output_limit_bytes, self._output_limit_bytes)
        )
        master_fd: int | None = None
        if tty and os.name != "nt" and pty is not None:
            master_fd, slave_fd = pty.openpty()
            try:
                try:
                    os.set_blocking(master_fd, False)
                    process = await _spawn(
                        command,
                        cwd=cwd,
                        login=login,
                        shell=shell or self.shell,
                        env=env,
                        prepared_argv=prepared_argv,
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
                shell=shell or self.shell,
                env=env,
                prepared_argv=prepared_argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        session = _ProcessSession(
            session_id,
            process,
            output_limit,
            pty_master_fd=master_fd,
            tty=master_fd is not None,
            terminal_info=BackgroundTerminalInfo(item_id, session_id, command, cwd),
        )
        session.reader_task = asyncio.create_task(
            self._read_pty_output(session) if master_fd is not None else self._read_output(session)
        )
        if master_fd is not None:
            session.writer_task = asyncio.create_task(self._write_pty_input(session))
        if timeout_seconds is not None:
            session.timeout_task = asyncio.create_task(
                self._enforce_timeout(session, timeout_seconds)
            )
        for task in (session.reader_task, session.writer_task, session.timeout_task):
            if task is not None:
                task.add_done_callback(lambda task: self._record_failure(session, task))
        if publish:
            await self._publish_session(session)
        return session

    async def _publish_session(self, session: _ProcessSession) -> None:
        # Atomic selection/removal/publication precedes any victim cleanup await.
        victim = select_prunable(self._sessions.values())
        if victim is not None:
            self._sessions.pop(victim.id)
        self._sessions[session.id] = session
        if victim is not None:
            try:
                await self._retire(victim)
            except BaseException:
                await self._retire(session)
                raise

    def _record_failure(self, session: _ProcessSession, task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            failure = task.exception()
            if failure is not None and session.failure is None:
                session.failure = failure
                session.failure_event.set()
                # A missing next poll must not leave a failed process running.
                # Keep its failed session available for the eventual observation.
                self._ensure_termination(session)

    async def _wait_session(self, session: _ProcessSession, seconds: float) -> None:
        wait = asyncio.create_task(self._wait_at_most(session.process, seconds))
        failed = asyncio.create_task(session.failure_event.wait())
        try:
            await asyncio.wait((wait, failed), return_when=asyncio.FIRST_COMPLETED)
            if wait.done():
                wait.result()
        finally:
            wait.cancel()
            failed.cancel()
            await asyncio.gather(wait, failed, return_exceptions=True)

    async def write_stdin(
        self,
        session_id: str,
        chars: str,
        *,
        yield_seconds: float,
        permissions: ExecutionPermissions | None = None,
        policy_cwd: Path | None = None,
        call_id: str = "",
        review_enabled: bool = False,
    ) -> ProcessObservation:
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"unknown or completed process session: {session_id}")
        async with session.interaction_lock:
            if self._sessions.get(session_id) is not session:
                raise ValueError(f"unknown or completed process session: {session_id}")
            generation = self._generation
            reviewed_process = session.process
            reviewed_pty = session.pty_master_fd
            if self._closing:
                raise asyncio.CancelledError
            if review_enabled and chars and not (not session.tty and chars == "\x03"):
                await owned_terminal_review(
                    self,
                    session,
                    chars,
                    permissions,
                    policy_cwd or session.terminal_info.cwd,
                    call_id,
                )
                if self._closing or generation != self._generation:
                    raise asyncio.CancelledError
                if (
                    self._sessions.get(session_id) is not session
                    or session.process is not reviewed_process
                    or session.pty_master_fd != reviewed_pty
                ):
                    raise ValueError(f"unknown or completed process session: {session_id}")
            session.last_used = time.monotonic()
            return await self._write_stdin(session, chars, yield_seconds)

    async def _write_stdin(
        self, session: _ProcessSession, chars: str, yield_seconds: float
    ) -> ProcessObservation:
        if session.failure is None and chars:
            if session.pty_master_fd is not None:
                if session.process.returncode is None:
                    try:
                        await self._enqueue_pty_input(session, chars.encode())
                    except Exception:
                        await self._retire(session)
                        raise
                    await asyncio.sleep(0.1)
            elif chars == "\x03" and os.name != "nt":
                signal_owned_group(session.process.pid, signal.SIGINT)
            else:
                raise ValueError(
                    "stdin is closed for this session; rerun exec_command with tty=true "
                    "to keep stdin open"
                )
        started = time.monotonic()
        await self._wait_session(session, yield_seconds)
        return await self._observe(session, started)

    def list_background_terminals(self) -> tuple[BackgroundTerminalInfo, ...]:
        return tuple(
            session.terminal_info
            for session in sorted(self._sessions.values(), key=lambda session: session.id)
            if session.process.returncode is None
            and session.failure is None
            and session.terminal_info is not None
        )

    async def terminate_background_terminal(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False

        async def terminate() -> bool:
            try:
                await self._stop(session)
            except Exception:
                # Failed termination must not discard the owned process handle.
                return False
            await self._retire(session)
            return True

        task = asyncio.create_task(terminate(), name=f"corki-terminal-stop-{session_id}")
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        result = task.result()
        if cancelled:
            raise asyncio.CancelledError
        return result

    async def terminate_all(self) -> None:
        self._closing += 1
        self._generation += 1
        for admission_cancelled in self._starting.values():
            admission_cancelled.set()
        errors: list[BaseException] = []
        try:
            if await close_terminal_reviews(self):
                errors.append(asyncio.CancelledError())
            for startup in tuple(self._starting):
                while not startup.done():
                    try:
                        await asyncio.shield(startup)
                    except asyncio.CancelledError as exc:
                        # A startup stopped at our own generation fence is not
                        # a cancellation request for this cleanup caller.
                        if not startup.cancelled() or asyncio.current_task().cancelling():
                            errors.append(exc)
                    except Exception:
                        break  # Spawn failures belong to their execute caller.
                if not startup.cancelled():
                    startup.exception()
            for session in tuple(self._sessions.values()):
                try:
                    await self._retire(session)
                except BaseException as exc:
                    # One failed cleanup must not prevent the other sessions.
                    errors.append(exc)
        finally:
            self._closing -= 1
        if errors:
            raise errors[0]

    async def _retire(self, session: _ProcessSession) -> None:
        # Observation and shutdown may converge on the same process. Exactly one
        # owner closes it; caller cancellation must still join that cleanup.
        if session.cleanup_task is None:
            session.cleanup_task = asyncio.create_task(
                self._cleanup_session(session), name=f"corki-process-cleanup-{session.id}"
            )
        cancelled = False
        while not session.cleanup_task.done():
            try:
                await asyncio.shield(session.cleanup_task)
            except asyncio.CancelledError:
                cancelled = True
        session.cleanup_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _cleanup_session(self, session: _ProcessSession) -> None:
        try:
            await self._stop(session)
        finally:
            tasks = [
                t
                for t in (session.reader_task, session.writer_task, session.timeout_task)
                if t is not None
            ]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._close_pty(session)
            # asyncio has no public Process.close for inherited pipe handles.
            # Close only this manager-owned subprocess transport, after termination.
            transport = getattr(session.process, "_transport", None)
            if transport is not None:
                transport.close()
            if self._sessions.get(session.id) is session:
                self._sessions.pop(session.id)

    async def _stop(self, session: _ProcessSession) -> None:
        self._ensure_termination(session)
        assert session.termination_task is not None
        await asyncio.shield(session.termination_task)

    def _ensure_termination(self, session: _ProcessSession) -> None:
        if session.termination_task is None:
            session.termination_task = asyncio.create_task(self._terminate(session.process))
            session.termination_task.add_done_callback(
                lambda task: self._record_failure(session, task)
            )

    async def _observe(self, session: _ProcessSession, started: float) -> ProcessObservation:
        # Inspect tasks too: their done callbacks may not yet have been scheduled.
        for task in (session.reader_task, session.writer_task, session.timeout_task):
            if task is not None and task.done():
                self._record_failure(session, task)
        if session.failure is not None:
            try:
                await self._retire(session)
            except Exception as exc:
                if exc is session.failure:
                    raise
                raise session.failure from exc
            raise session.failure
        running = session.process.returncode is None
        if not running:
            try:
                if session.reader_task is not None:
                    with suppress(TimeoutError):
                        await asyncio.wait_for(
                            asyncio.shield(session.reader_task), _POST_EXIT_DRAIN_SECONDS
                        )
            finally:
                await self._retire(session)
        if session.failure is not None:
            raise session.failure
        original_bytes = len(session.head) + session.buffered_bytes + session.dropped_bytes
        metadata = {
            "terminal_info": session.terminal_info,
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
        return ProcessObservation(
            output=output,
            exit_code=observation_exit_code(session.process.returncode, tty=session.tty),
            session_id=None,
            timed_out=session.timed_out,
            **metadata,
        )

    @staticmethod
    async def _wait_at_most(process: asyncio.subprocess.Process, seconds: float) -> None:
        # Process.wait may await pipe EOF even after waitpid has reaped the leader.
        # returncode reflects that independent exit notification. No orphan wait task.
        deadline = time.monotonic() + seconds
        while process.returncode is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.01, remaining))

    @staticmethod
    async def _read_output(session: _ProcessSession) -> None:
        assert session.process.stdout is not None
        while chunk := await session.process.stdout.read(4096):
            session.append(chunk)

    @staticmethod
    async def _read_pty_output(session: _ProcessSession) -> None:
        assert session.pty_master_fd is not None
        descriptor = session.pty_master_fd
        while chunk := await read_pty(descriptor):
            session.append(chunk)

    @staticmethod
    async def _write_pty_input(session: _ProcessSession) -> None:
        assert session.pty_master_fd is not None
        descriptor = session.pty_master_fd
        while True:
            data = await session.input_queue.get()
            await write_pty(descriptor, data)

    @staticmethod
    async def _enqueue_pty_input(session: _ProcessSession, data: bytes) -> None:
        writer = session.writer_task
        if writer is None or writer.done():
            if writer is not None and not writer.cancelled() and writer.exception() is not None:
                raise writer.exception()
            raise ValueError("process stdin is closed")
        enqueue = asyncio.create_task(session.input_queue.put(data))
        try:
            await asyncio.wait((enqueue, writer), return_when=asyncio.FIRST_COMPLETED)
            if writer.done():
                if not writer.cancelled() and writer.exception() is not None:
                    raise writer.exception()
                raise ValueError("process stdin is closed")
            enqueue.result()
        finally:
            enqueue.cancel()
            await asyncio.gather(enqueue, return_exceptions=True)

    async def _enforce_timeout(self, session: _ProcessSession, seconds: float) -> None:
        try:
            await asyncio.sleep(seconds)
            if session.process.returncode is None:
                session.timed_out = True
                session.append(f"\nCommand timed out after {seconds:g}s.\n".encode())
                await self._stop(session)
        except asyncio.CancelledError:
            return

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            if os.name != "nt":
                signal_owned_group(process.pid, signal.SIGKILL)
            return
        try:
            if os.name != "nt":
                signal_owned_group(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            # The child may have exited between the returncode check and the
            # signal. Waiting still reaps the asyncio transport.
            await ProcessManager._wait_at_most(process, _TERMINATE_GRACE_SECONDS)
            if process.returncode is None:
                raise TimeoutError("owned process group disappeared before leader exit") from None
            return
        try:
            await ProcessManager._wait_at_most(process, _TERMINATE_GRACE_SECONDS)
        finally:
            # The leader exiting on SIGTERM does not prove its descendants exited.
            if os.name != "nt" or process.returncode is None:
                try:
                    if os.name != "nt":
                        signal_owned_group(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass
                await ProcessManager._wait_at_most(process, _TERMINATE_GRACE_SECONDS)
            if process.returncode is None:
                raise TimeoutError("owned process did not exit after termination")

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
    shell: Shell | None = None,
    env: dict[str, str] | None = None,
    prepared_argv: list[str] | None = None,
) -> asyncio.subprocess.Process:
    argv = (
        prepared_argv
        if prepared_argv is not None
        else (shell or default_user_shell()).exec_args(command, login=login)
    )
    if os.name != "nt" and type(stdin) is int and stdin >= 0 and stdin == stdout == stderr:
        return await spawn_pty(
            argv,
            cwd=cwd,
            slave_fd=stdin,
            terminate=ProcessManager._terminate,
            env=env,
        )
    options = {
        "cwd": cwd,
        "stdin": stdin,
        "stdout": stdout,
        "stderr": stderr,
        "start_new_session": os.name != "nt",
        "env": env,
    }
    return await asyncio.create_subprocess_exec(*argv, **options)
