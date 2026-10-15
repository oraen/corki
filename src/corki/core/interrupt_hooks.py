"""Turn interruption diagnostics: no control output and no model context."""

import asyncio
import json
from dataclasses import asdict

from corki.core.prompt_hook_output import PromptHookOutput
from corki.core.stop_hooks import MCPHook, _execution_request, restore_command, run_command
from corki.protocol.events import (
    HookCompleted,
    HookOutputEntry,
    HookRunSummary,
    HookStarted,
    WarningEvent,
)
from corki.protocol.session_source import SessionSourceKind


def outcome(result):
    error = result.get("error")
    code = result.get("exit_code")
    if not error and code != 0:
        error = (
            f"hook exited with code {code}"
            if code is not None
            else "hook exited without a status code"
        )
    if error:
        return PromptHookOutput(status="failed", entries=(HookOutputEntry("error", str(error)),))
    raw = result.get("stdout", "").strip()
    if not raw:
        return PromptHookOutput()
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) - {"systemMessage"}:
            raise ValueError("Invalid interrupt output fields")
        message = value.get("systemMessage")
        if message is not None and not isinstance(message, str):
            raise ValueError("systemMessage must be a string")
        return PromptHookOutput(
            entries=() if message is None else (HookOutputEntry("warning", message),)
        )
    except (ValueError, RecursionError):
        error = (
            "hook returned invalid interrupt hook JSON output"
            if raw.startswith(("{", "["))
            else "Interrupt hook returned non-JSON stdout"
        )
        return PromptHookOutput(status="failed", entries=(HookOutputEntry("error", error),))


class FinalEvents:
    """Cancellation diagnostics cannot block on an abandoned bounded event queue."""

    def __init__(self, items):
        self.items = items

    async def emit(self, event):
        self.items.append(event)


def validate_plan_identity(plan, session, turn):
    payload = plan.get("payload") if isinstance(plan, dict) else None
    if (
        not isinstance(payload, dict)
        or type(plan.get("version")) is not int
        or plan["version"] != 1
        or payload.get("turn_id") != str(turn)
        or payload.get("hook_event_name") != "Interrupt"
        or payload.get("session_id") != str(session)
        or not isinstance(plan.get("commands"), list)
        or not plan["commands"]
    ):
        raise ValueError("Interrupt plan identity mismatch")


def restore_plan(plan, session, turn):
    """Validate durable data before using it as cancellation or execution evidence."""
    validate_plan_identity(plan, session, turn)
    payload = plan["payload"]
    text_fields = {"session_id", "turn_id", "hook_event_name", "cwd", "model", "permission_mode"}
    if (
        set(plan) != {"version", "payload", "commands"}
        or set(payload) != text_fields | {"transcript_path"}
        or any(not isinstance(payload[field], str) for field in text_fields)
        or not payload["cwd"]
        or payload["permission_mode"] not in {"default", "bypassPermissions"}
        or (
            payload["transcript_path"] is not None
            and not isinstance(payload["transcript_path"], str)
        )
    ):
        raise ValueError("Invalid durable Interrupt payload")
    commands, keys = [], set()
    try:
        for entry in plan["commands"]:
            environment = entry["environment"]
            if not isinstance(environment, list) or any(
                not isinstance(pair, list)
                or len(pair) != 2
                or any(not isinstance(part, str) for part in pair)
                for pair in environment
            ):
                raise ValueError("Invalid Interrupt environment")
            command = restore_command({**entry, "environment": tuple(map(tuple, environment))})
            if (
                not isinstance(command.key, str)
                or not command.key
                or command.key in keys
                or not isinstance(command.fingerprint, str)
                or not isinstance(command.command, str)
                or type(command.timeout) is not int
                or not 1 <= command.timeout <= 3
                or type(command.asynchronous) is not bool
                or type(command.additional_context_limit) is not int
                or command.additional_context_limit < 0
                or any(
                    value is not None and not isinstance(value, str)
                    for value in (command.status_message, command.matcher)
                )
                or (isinstance(command, MCPHook) and not isinstance(command.mcp, dict))
            ):
                raise ValueError("Invalid Interrupt command fields")
            keys.add(command.key)
            commands.append(command)
    except (TypeError, KeyError, ValueError) as error:
        raise ValueError("Invalid durable Interrupt commands") from error
    return payload, tuple(commands)


async def has_durable_interrupt(repository, thread, turn):
    """Older sessions may have a shutdown plan but no separate cancellation intent."""
    saved = await repository.load_hook_batch(thread, turn, f"interrupt_hook:{turn}:")
    if saved is None:
        return False
    restore_plan(saved[0], await repository.load_thread_session_id(thread), turn)
    return True


async def run(
    owner, *, repository, thread, turn, session_source, settings, shell, mcp_manager, events
):
    if session_source.kind == SessionSourceKind.SUBAGENT:
        return
    prefix = f"interrupt_hook:{turn}:"
    saved = await repository.load_hook_batch(thread, turn, prefix)
    current_commands, warnings = owner._snapshot.get("Interrupt", ((), ()))
    for warning in warnings:
        await events.emit(WarningEvent(thread, turn, warning))
    if saved is None:
        if not current_commands:
            return
        transcript = None
        try:
            path = await repository.materialize_transcript(thread)
            transcript = str(path) if path is not None else None
        except OSError as error:
            await events.emit(
                WarningEvent(thread, turn, f"Interrupt transcript unavailable: {error}")
            )
        payload = {
            "session_id": str(await repository.load_thread_session_id(thread)),
            "turn_id": str(turn),
            "hook_event_name": "Interrupt",
            "cwd": str(settings.working_directory),
            "model": settings.model,
            "transcript_path": transcript,
            "permission_mode": "bypassPermissions"
            if settings.execution_permissions is not None
            and settings.execution_permissions.approval_policy_json == '"never"'
            else "default",
        }
        commands, records = current_commands, {}
        await repository.save_hook_batch(
            thread,
            turn,
            prefix,
            {"version": 1, "payload": payload, "commands": [asdict(c) for c in commands]},
        )
    else:
        plan, records = saved
        payload, commands = restore_plan(
            plan, await repository.load_thread_session_id(thread), turn
        )
        if set(records) - {prefix + command.key for command in commands}:
            raise ValueError("Unexpected interrupt execution record")
    current = {command.key: command for command in current_commands}

    async def execute(command, key, request):
        try:
            if isinstance(command, MCPHook):
                from corki.core.mcp_tool_hooks import run as run_mcp

                result = await run_mcp(command, payload, mcp_manager, thread)
            else:
                result = await run_command(
                    command,
                    payload,
                    shell=shell,
                    cwd=settings.working_directory,
                    environment={**owner.environment, **dict(command.environment)},
                )
        except (OSError, ValueError, TimeoutError) as error:
            result = {"error": f"Interrupt failed: {error}"}
        await repository.complete_hook_execution(thread, turn, key, request, result)
        return result

    async def invoke(command):
        key = prefix + command.key
        request = _execution_request(command, payload)
        record = records.get(key)
        if record is not None:
            if record["request"] != request:
                raise ValueError("Interrupt execution identity mismatch")
            if record["result"] is None:
                if command.asynchronous and owner._async.owns(key, request):
                    return None
                return {"error": "Interrupt outcome unknown; not repeated"}
            return record["result"]
        if current.get(command.key) != command:
            return {"error": "Interrupt authorization changed; not resumed"}
        result = await repository.claim_hook_execution(thread, turn, key, request)
        if command.asynchronous:

            async def background():
                parsed = outcome(
                    result if result is not None else await execute(command, key, request)
                )
                return (
                    "\n".join(entry.text for entry in parsed.entries if entry.kind == "error"),
                    tuple(entry.text for entry in parsed.entries if entry.kind == "warning"),
                )

            if result is None:
                owner._async.start(key, request, background)
            return None
        return result if result is not None else await execute(command, key, request)

    for command in commands:
        if not command.asynchronous:
            await events.emit(
                HookStarted(
                    thread,
                    turn,
                    HookRunSummary(
                        prefix + command.key,
                        command.key,
                        "Interrupt",
                        "running",
                        command.status_message,
                    ),
                )
            )
    tasks = [asyncio.create_task(invoke(command)) for command in commands]
    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        joined = asyncio.gather(*tasks, return_exceptions=True)
        while not joined.done():
            try:
                await asyncio.shield(joined)
            except asyncio.CancelledError:
                continue
    error = None
    for command, result in zip(commands, results, strict=True):
        if isinstance(result, BaseException):
            error = error or result
            result = {"error": "Interrupt execution was not committed; not repeated"}
        if not command.asynchronous:
            parsed = outcome(result)
            await events.emit(
                HookCompleted(
                    thread,
                    turn,
                    HookRunSummary(
                        prefix + command.key,
                        command.key,
                        "Interrupt",
                        parsed.status,
                        command.status_message,
                        parsed.entries,
                    ),
                )
            )
    if error is not None:
        raise error
