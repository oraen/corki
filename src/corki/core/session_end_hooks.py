"""Bounded, synchronous session teardown effects, after execution services stop."""

import asyncio
from dataclasses import asdict

from corki.context.hosted_output import truncate_output_text
from corki.core.hook_matchers import matches
from corki.core.stop_hooks import _execution_request, run_command
from corki.protocol.events import (
    HookCompleted,
    HookOutputEntry,
    HookRunSummary,
    HookStarted,
    WarningEvent,
)
from corki.protocol.ids import new_turn_id
from corki.protocol.session_source import SessionSourceKind
from corki.protocol.truncation import TruncationPolicy


class ShutdownEvents:
    """A finite shutdown event log shared by concurrent close observers."""

    def __init__(self):
        self.items = []
        self.changed = asyncio.Event()

    async def emit(self, event):
        self.items.append(event)
        self.changed.set()


async def run(owner, *, repository, thread, session_source, settings, shell, events):
    if session_source.kind == SessionSourceKind.SUBAGENT:
        return
    commands, warnings = owner._snapshot.get("SessionEnd", ((), ()))
    commands = tuple(command for command in commands if matches(command.matcher, "other"))
    if not commands and not warnings:
        return
    identity = new_turn_id()  # Hook notification identity, not a new user Turn.
    for warning in warnings:
        await events.emit(WarningEvent(thread, identity, warning))
    if not commands:
        return
    transcript = None
    try:
        path = await repository.materialize_transcript(thread)
        transcript = str(path) if path is not None else None
    except OSError as error:
        await events.emit(
            WarningEvent(thread, identity, f"SessionEnd transcript unavailable: {error}")
        )
    payload = {
        "session_id": str(await repository.load_thread_session_id(thread)),
        "transcript_path": transcript,
        "cwd": str(settings.working_directory),
        "hook_event_name": "SessionEnd",
        "reason": "other",
    }
    prefix = f"session_end:{identity}:"
    await repository.save_hook_batch(
        thread,
        identity,
        prefix,
        {
            "version": 1,
            "payload": payload,
            "commands": [asdict(command) for command in commands],
        },
    )
    for command in commands:
        await events.emit(
            HookStarted(
                thread,
                identity,
                HookRunSummary(
                    prefix + command.key,
                    command.key,
                    "SessionEnd",
                    "running",
                    command.status_message,
                ),
            )
        )

    async def execute(command):
        key = prefix + command.key
        request = _execution_request(command, payload)
        result = await repository.claim_hook_execution(thread, identity, key, request)
        if result is not None:
            return result
        try:
            result = await run_command(
                command,
                payload,
                shell=shell,
                cwd=settings.working_directory,
                environment={**owner.environment, **dict(command.environment)},
            )
        except (OSError, ValueError, TimeoutError) as error:
            result = {"error": f"SessionEnd failed: {type(error).__name__}: {error}"}
        await repository.complete_hook_execution(thread, identity, key, request, result)
        return result

    tasks = [asyncio.create_task(execute(command)) for command in commands]
    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        joined = asyncio.gather(*tasks, return_exceptions=True)
        cancelled = False
        while not joined.done():
            try:
                await asyncio.shield(joined)
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError
    fatal = None
    for command, result in zip(commands, results, strict=True):
        if isinstance(result, BaseException):
            fatal = fatal or result
            error = f"SessionEnd execution was not committed: {type(result).__name__}: {result}"
        else:
            error = result.get("error")
            if not error and result.get("exit_code") != 0:
                error = (
                    result.get("stderr", "").strip()
                    or f"SessionEnd exited with status {result.get('exit_code')}"
                )
        entries = (
            (
                HookOutputEntry(
                    "error", truncate_output_text(str(error), TruncationPolicy("tokens", 2500))
                ),
            )
            if error
            else ()
        )
        await events.emit(
            HookCompleted(
                thread,
                identity,
                HookRunSummary(
                    prefix + command.key,
                    command.key,
                    "SessionEnd",
                    "failed" if error else "completed",
                    command.status_message,
                    entries,
                ),
            )
        )
    if fatal is not None:
        raise fatal
