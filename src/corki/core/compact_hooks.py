"""Trusted compaction lifecycle effects, outside the model transport retry loop."""

import asyncio
import json
from dataclasses import asdict

from corki.core.hook_matchers import matches
from corki.core.stop_hooks import MCPHook, _execution_request, restore_command, run_command
from corki.protocol.events import (
    HookCompleted,
    HookOutputEntry,
    HookRunSummary,
    HookStarted,
    WarningEvent,
)
from corki.protocol.session_source import SessionSourceKind, SubAgentSource, ThreadSpawnSource


def outcome(result, *, control=True):
    error = result.get("error")
    if error:
        return False, "failed", (HookOutputEntry("error", str(error)),)
    if result.get("exit_code") != 0:
        return (
            False,
            "failed",
            (
                HookOutputEntry(
                    "error", result.get("stderr", "").strip() or "Hook did not exit successfully"
                ),
            ),
        )
    text = result.get("stdout", "").strip()
    if not text or not text.startswith(("{", "[")):
        return False, "completed", ()
    try:
        value = json.loads(text)
    except ValueError:
        if not text.startswith(("{", "[")):
            return False, "completed", ()
        value = None
    valid = isinstance(value, dict) and not set(value) - {
        "continue",
        "stopReason",
        "suppressOutput",
        "systemMessage",
    }
    if valid:
        valid = all(type(value[k]) is bool for k in ("continue", "suppressOutput") if k in value)
        valid = valid and all(
            value.get(k) is None or isinstance(value[k], str)
            for k in ("stopReason", "systemMessage")
        )
    if not valid:
        return False, "failed", (HookOutputEntry("error", "Invalid compaction hook JSON output"),)
    entries = (
        ()
        if value.get("systemMessage") is None
        else (HookOutputEntry("warning", value["systemMessage"]),)
    )
    stopped = control and value.get("continue") is False
    if stopped:
        entries += (
            HookOutputEntry("stop", value.get("stopReason") or "Compaction hook stopped execution"),
        )
    return stopped, "stopped" if stopped else "completed", entries


async def run(
    owner,
    event,
    operation,
    model,
    trigger,
    *,
    state,
    runtime,
    repository,
    settings,
    shell,
    mcp_manager,
    stage="run",
):
    thread, turn = state["thread_id"], state["turn_id"]
    commands, warnings = owner._snapshot.get(event, ((), ()))
    commands = tuple(command for command in commands if matches(command.matcher, trigger))
    prefix = f"compact_hook:{turn}:{event}:{operation}:"
    receipt_key = prefix + "receipt"
    receipt = await repository.load_hook_batch(thread, turn, receipt_key)
    if receipt is not None:
        value, executions = receipt
        if (
            executions
            or type(value.get("version")) is not int
            or value["version"] != 1
            or type(value.get("stopped")) is not bool
        ):
            raise ValueError("Invalid compaction hook completion receipt")
        if value["stopped"]:
            raise asyncio.CancelledError
        return
    saved = await repository.load_hook_batch(thread, turn, prefix)
    if stage == "recover" and saved is None:
        # Older markers have no operation-bound plan. Current configuration
        # must never retroactively introduce effects into their history.
        if commands:
            await runtime.events.emit(
                WarningEvent(
                    thread,
                    turn,
                    "Legacy compaction has no PostCompact plan; new hooks were not applied",
                )
            )
        return
    current = {command.key: command for command in commands}
    if saved is None and not commands and not warnings:
        await repository.save_hook_batch(
            thread, turn, receipt_key, {"version": 1, "stopped": False}
        )
        return
    if stage != "prepare":
        for warning in warnings:
            await runtime.events.emit(WarningEvent(thread, turn, warning))
    if saved is None:
        payload = {
            "session_id": str(await repository.load_thread_session_id(thread)),
            "turn_id": str(turn),
            "cwd": str(settings.working_directory),
            "hook_event_name": event,
            "model": model,
            "trigger": trigger,
            "transcript_path": await repository.materialize_transcript(thread),
        }
        if payload["transcript_path"] is not None:
            payload["transcript_path"] = str(payload["transcript_path"])
        source = runtime.session_source
        if (
            source.kind == SessionSourceKind.SUBAGENT
            and isinstance(source.value, SubAgentSource)
            and source.value.variant == "thread_spawn"
            and isinstance(source.value.value, ThreadSpawnSource)
        ):
            role = source.value.value.agent_role
            payload.update(agent_id=str(thread), agent_type=role if role is not None else "default")
        snapshot = {"version": 1, "payload": payload, "commands": [asdict(c) for c in commands]}
        await repository.save_hook_batch(thread, turn, prefix, snapshot)
        records = {}
    else:
        snapshot, records = saved
        if type(snapshot.get("version")) is not int or snapshot["version"] != 1:
            raise ValueError("Invalid compaction hook snapshot")
        payload = snapshot["payload"]
        if (
            not isinstance(payload, dict)
            or not all(
                isinstance(payload.get(key), str)
                for key in ("session_id", "turn_id", "hook_event_name", "model", "cwd", "trigger")
            )
            or payload["trigger"] not in ("manual", "auto")
            or "transcript_path" not in payload
            or (
                payload["transcript_path"] is not None
                and not isinstance(payload["transcript_path"], str)
            )
            or ("agent_id" in payload) != ("agent_type" in payload)
            or any(
                not isinstance(payload[key], str)
                for key in ("agent_id", "agent_type")
                if key in payload
            )
        ):
            raise ValueError("Invalid compaction hook payload")
        if payload.get("hook_event_name") != event or payload.get("turn_id") != str(turn):
            raise ValueError("Compaction hook identity mismatch")
        session = str(await repository.load_thread_session_id(thread))
        # Early v1 snapshots used the owning thread ID instead of its session.
        # Accept that exact legacy identity without rewriting claimed requests.
        if payload.get("session_id") not in (session, str(thread)):
            raise ValueError("Compaction hook session identity mismatch")
        commands = tuple(
            restore_command(
                {**entry, "environment": tuple(tuple(pair) for pair in entry["environment"])}
            )
            for entry in snapshot["commands"]
        )
        if set(records) - {prefix + c.key for c in commands}:
            raise ValueError("Unexpected compaction hook execution")
        for command in commands:
            key = prefix + command.key
            record = records.get(key)
            request = _execution_request(command, payload)
            if record is not None:
                if record["request"] != request:
                    raise ValueError("Compaction hook request identity mismatch")
                if record["result"] is None and not owner._async.owns(key, request):
                    raise RuntimeError("Compaction hook outcome unknown; not repeated")
            elif current.get(command.key) != command:
                raise RuntimeError("Compaction hook authorization changed; not resumed")

    if stage == "prepare":
        return

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
            result = {"error": f"Compaction hook failed: {type(error).__name__}: {error}"}
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
                raw = result if result is not None else await execute(command, key, request)
                _, _, entries = outcome(raw, control=False)
                return "", tuple(entry.text for entry in entries)

            if result is None:
                owner._async.start(key, request, background)
            else:
                owner._async.publish(key, await background())
            return None
        if result is None:
            result = await execute(command, key, request)
        return result

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
        command_stopped, status, entries = outcome(result)
        stopped |= command_stopped
        await runtime.events.emit(
            HookCompleted(
                thread,
                turn,
                HookRunSummary(
                    prefix + command.key,
                    command.key,
                    event,
                    status,
                    command.status_message,
                    entries,
                ),
            )
        )
    await repository.save_hook_batch(thread, turn, receipt_key, {"version": 1, "stopped": stopped})
    if stopped:
        raise asyncio.CancelledError
