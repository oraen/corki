"""Session-owned start obligations and durable execution before input admission."""

import asyncio
import json
from dataclasses import asdict, replace

from corki.core.hook_context import prepare_context
from corki.core.hook_matchers import matches
from corki.core.prompt_hook_output import PromptHookOutput
from corki.core.prompt_hook_output import outcome as prompt_output
from corki.core.stop_hooks import MCPHook, _execution_request, restore_command, run_command
from corki.protocol.events import (
    HookCompleted,
    HookOutputEntry,
    HookRunSummary,
    HookStarted,
    WarningEvent,
)
from corki.protocol.session_source import SessionSourceKind, SubAgentSource, ThreadSpawnSource


def outcome(result, *, event="SessionStart", control=True):
    """Start hooks share context wire fields, not prompt decision/exit-2 control."""
    error = result.get("error")
    if error or result.get("exit_code") != 0:
        return PromptHookOutput(
            status="failed",
            entries=(
                HookOutputEntry(
                    "error",
                    str(error)
                    if error
                    else f"{event} exited with status {result.get('exit_code')}",
                ),
            ),
        )
    raw = result.get("stdout", "").strip()
    if raw.startswith(("{", "[")):
        try:
            value = json.loads(raw)
            if isinstance(value, dict) and set(value) & {"decision", "reason"}:
                raise ValueError("Start hook output does not accept decision/reason")
        except (ValueError, RecursionError) as error:
            return PromptHookOutput(
                status="failed", entries=(HookOutputEntry("error", str(error)),)
            )
    return prompt_output(result, control=control and event == "SessionStart")


def plan_key(turn):
    return f"start_hook:{turn}:initial:"


async def allows_input(repository, thread, turn):
    """An unfinished or stopped start check cannot admit the original input in cleanup."""
    saved = await repository.load_hook_batch(thread, turn, plan_key(turn))
    if saved is None:
        return True
    receipt = await repository.load_hook_batch(thread, turn, plan_key(turn) + "receipt")
    if receipt is None:
        return False
    return not _stopped(receipt)


def _stopped(receipt):
    value, records = receipt
    if (
        records
        or set(value) != {"version", "stopped"}
        or type(value.get("version")) is not int
        or value["version"] != 1
        or type(value.get("stopped")) is not bool
    ):
        raise ValueError("Invalid start hook receipt")
    return value["stopped"]


async def run(owner, *, state, runtime, repository, settings, shell, mcp_manager, operation=None):
    thread, turn = state["thread_id"], state["turn_id"]
    prefix = plan_key(turn) if operation is None else f"start_hook:{turn}:compact:{operation}:"
    saved = await repository.load_hook_batch(thread, turn, prefix)
    if saved is None and owner.start_source is None and operation is None:
        return False
    receipt = await repository.load_hook_batch(thread, turn, prefix + "receipt")
    if receipt is not None:
        if operation is None:
            owner.start_source = None
        return _stopped(receipt)
    source = owner.start_source if operation is None else "compact"
    event = "SessionStart"
    agent = None
    provenance = runtime.session_source
    if provenance.kind == SessionSourceKind.SUBAGENT:
        if (
            source == "startup"
            and isinstance(provenance.value, SubAgentSource)
            and provenance.value.variant == "thread_spawn"
            and isinstance(provenance.value.value, ThreadSpawnSource)
        ):
            event, agent = "SubagentStart", provenance.value.value
        elif saved is None:
            if operation is None:
                owner.start_source = None
            return False
    if saved is not None:
        snapshot, records = saved
        if type(snapshot.get("version")) is not int or snapshot["version"] != 1:
            raise ValueError("Invalid start hook plan")
        payload = snapshot["payload"]
        event = payload.get("hook_event_name")
        if event not in {"SessionStart", "SubagentStart"} or payload.get("session_id") != str(
            await repository.load_thread_session_id(thread)
        ):
            raise ValueError("Start hook identity mismatch")
        source = payload.get("source") if event == "SessionStart" else payload.get("agent_type")
    else:
        source = (
            (agent.agent_role if agent.agent_role is not None else "default") if agent else source
        )
    commands, warnings = owner._snapshot.get(event, ((), ()))
    commands = tuple(command for command in commands if matches(command.matcher, source))
    current = {command.key: command for command in commands}
    if saved is None and not commands and not warnings:
        if operation is None:
            owner.start_source = None
        return False
    if saved is None:
        transcript = None
        if commands:
            try:
                path = await repository.materialize_transcript(thread)
                transcript = str(path) if path is not None else None
            except OSError:
                pass
        payload = {
            "session_id": str(await repository.load_thread_session_id(thread)),
            "hook_event_name": event,
            "cwd": str(settings.working_directory),
            "model": settings.model,
            "transcript_path": transcript,
            "permission_mode": "bypassPermissions"
            if settings.execution_permissions is not None
            and settings.execution_permissions.approval_policy_json == '"never"'
            else "default",
        }
        if event == "SessionStart":
            payload["source"] = source
        else:
            payload.update(turn_id=str(turn), agent_id=str(thread), agent_type=source)
        await repository.save_hook_batch(
            thread,
            turn,
            prefix,
            {
                "version": 1,
                "payload": payload,
                "commands": [asdict(c) for c in commands],
            },
        )
    else:
        commands = tuple(
            restore_command({**entry, "environment": tuple(tuple(p) for p in entry["environment"])})
            for entry in snapshot["commands"]
        )
        if set(records) - {prefix + command.key for command in commands}:
            raise ValueError("Unexpected start hook execution")
        for command in commands:
            key = prefix + command.key
            record = records.get(key)
            request = _execution_request(command, payload)
            if record is not None:
                if record["request"] != request:
                    raise ValueError("Start hook request mismatch")
                if record["result"] is None and not owner._async.owns(key, request):
                    raise RuntimeError("Start hook outcome unknown; not repeated")
            elif current.get(command.key) != command:
                raise RuntimeError("Start hook authorization changed; not resumed")
    if operation is None:
        owner.start_source = None
    for warning in warnings:
        await runtime.events.emit(WarningEvent(thread, turn, warning))

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
            result = {"error": f"{event} failed: {error}"}
        await repository.complete_hook_execution(thread, turn, key, request, result)
        return result

    async def invoke(command):
        key = prefix + command.key
        request = _execution_request(command, payload)
        if command.asynchronous and owner._async.owns(key, request):
            return None
        result = await repository.claim_hook_execution(thread, turn, key, request)
        if command.asynchronous:

            async def background():
                if result is None:
                    await execute(command, key, request)
                return "", ()

            if result is None:
                owner._async.start(key, request, background)
            return None
        return result if result is not None else await execute(command, key, request)

    for command in commands:
        if not command.asynchronous:
            await runtime.events.emit(
                HookStarted(
                    thread,
                    turn,
                    HookRunSummary(
                        prefix + command.key, command.key, event, "running", command.status_message
                    ),
                )
            )
    tasks = [asyncio.create_task(invoke(command)) for command in commands]
    try:
        results = await asyncio.gather(*tasks)
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
    stopped = False
    for command, result in zip(commands, results, strict=True):
        if command.asynchronous:
            continue
        parsed = outcome(result, event=event)
        stopped |= parsed.stopped
        entries = parsed.entries
        if parsed.context and parsed.context.strip():
            context = await prepare_context(
                repository,
                state,
                prefix + command.key,
                parsed.context,
                command.additional_context_limit,
            )
            await repository.append_items(thread, (replace(context, source_input_id=None),))
            entries += (HookOutputEntry("context", context.content),)
        await runtime.events.emit(
            HookCompleted(
                thread,
                turn,
                HookRunSummary(
                    prefix + command.key,
                    command.key,
                    event,
                    parsed.status,
                    command.status_message,
                    entries,
                ),
            )
        )
    await repository.save_hook_batch(
        thread, turn, prefix + "receipt", {"version": 1, "stopped": stopped}
    )
    return stopped


async def drain(repository, thread, turn, events):
    for source_turn, prefix in await repository.load_hook_batch_keys(thread, "start_hook:"):
        if not prefix.endswith(":"):
            continue
        batch, records = await repository.load_hook_batch(thread, source_turn, prefix)
        payload = batch["payload"]
        for entry in batch["commands"]:
            if not entry["asynchronous"]:
                continue
            command = restore_command(
                {**entry, "environment": tuple(tuple(p) for p in entry["environment"])}
            )
            key = prefix + command.key
            record = records.get(key)
            if record is None or record["result"] is None:
                continue
            if record["request"] != _execution_request(command, payload):
                raise ValueError("Asynchronous start hook request mismatch")
            receipt_key = "async_start_delivered:" + key
            receipt = await repository.load_hook_batch(thread, source_turn, receipt_key)
            if receipt is not None:
                _stopped(receipt)
                continue
            parsed = outcome(record["result"], event=payload["hook_event_name"], control=False)
            if parsed.context and parsed.context.strip():
                context = await prepare_context(
                    repository,
                    {"thread_id": thread, "turn_id": source_turn, "request_items": ()},
                    key + ":async",
                    parsed.context,
                    command.additional_context_limit,
                )
                await repository.append_items(thread, (context,))
            for entry in parsed.entries:
                if entry.kind == "warning":
                    await events.emit(WarningEvent(thread, turn, entry.text))
            await repository.save_hook_batch(
                thread, source_turn, receipt_key, {"version": 1, "stopped": False}
            )
