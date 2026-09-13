"""Durable inspection of a user input before conversation admission."""

import asyncio
from dataclasses import asdict, replace

from corki.core.hook_context import prepare_context
from corki.core.prompt_hook_output import outcome
from corki.core.stop_hooks import MCPHook, _execution_request, restore_command, run_command
from corki.protocol.events import (
    HookCompleted,
    HookOutputEntry,
    HookRunSummary,
    HookStarted,
    WarningEvent,
)
from corki.protocol.items import ContextItem, item_from_payload, item_to_payload
from corki.protocol.session_source import SessionSourceKind, SubAgentSource, ThreadSpawnSource


def receipt_key(item):
    return f"user_prompt_submit:{item.turn_id}:{item.id}:receipt"


async def rejected(repository, thread, item):
    saved = await repository.load_hook_batch(thread, item.turn_id, receipt_key(item))
    if saved is None:
        return False
    value, records = saved
    if (
        records
        or type(value.get("version")) is not int
        or value["version"] != 1
        or type(value.get("stopped")) is not bool
        or value.get("prompt") != item.content
    ):
        raise ValueError("Invalid user prompt admission receipt")
    return value["stopped"]


async def ordered_inputs(repository, thread, items):
    """Deliver synchronous feedback immediately after its accepted input."""
    ordered = []
    for item in items:
        ordered.append(item)
        saved = await repository.load_hook_batch(thread, item.turn_id, receipt_key(item))
        if saved is None:
            continue
        if await rejected(repository, thread, item):
            raise ValueError("Rejected input cannot be admitted")
        for payload in saved[0].get("contexts", ()):
            context = item_from_payload("context", payload)
            if (
                not isinstance(context, ContextItem)
                or context.source_input_id != item.id
                or context.turn_id != item.turn_id
            ):
                raise ValueError("Invalid user prompt context identity")
            ordered.append(context)
    return tuple(ordered)


async def inspect(owner, item, *, state, runtime, repository, settings, shell, mcp_manager):
    thread, turn = state["thread_id"], state["turn_id"]
    prefix = f"user_prompt_submit:{turn}:{item.id}:"
    receipt = await repository.load_hook_batch(thread, turn, receipt_key(item))
    if receipt is not None:
        return await rejected(repository, thread, item)
    commands, warnings = owner._snapshot.get("UserPromptSubmit", ((), ()))
    current = {command.key: command for command in commands}
    saved = await repository.load_hook_batch(thread, turn, prefix)
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
            "turn_id": str(turn),
            "hook_event_name": "UserPromptSubmit",
            "prompt": item.content,
            "cwd": str(settings.working_directory),
            "model": settings.model,
            "transcript_path": transcript,
            "permission_mode": "bypassPermissions"
            if settings.execution_permissions is not None
            and settings.execution_permissions.approval_policy_json == '"never"'
            else "default",
        }
        source = runtime.session_source
        if (
            source.kind == SessionSourceKind.SUBAGENT
            and isinstance(source.value, SubAgentSource)
            and source.value.variant == "thread_spawn"
            and isinstance(source.value.value, ThreadSpawnSource)
        ):
            payload.update(
                agent_id=str(thread), agent_type=source.value.value.agent_role or "default"
            )
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
        snapshot, records = saved
        if type(snapshot.get("version")) is not int or snapshot["version"] != 1:
            raise ValueError("Invalid user prompt hook plan")
        payload = snapshot["payload"]
        if (
            payload.get("prompt") != item.content
            or payload.get("turn_id") != str(turn)
            or payload.get("hook_event_name") != "UserPromptSubmit"
            or payload.get("session_id") != str(await repository.load_thread_session_id(thread))
        ):
            raise ValueError("User prompt hook identity mismatch")
        commands = tuple(
            restore_command({**entry, "environment": tuple(tuple(p) for p in entry["environment"])})
            for entry in snapshot["commands"]
        )
        if set(records) - {prefix + c.key for c in commands}:
            raise ValueError("Unexpected user prompt hook execution")
        for command in commands:
            key = prefix + command.key
            record = records.get(key)
            request = _execution_request(command, payload)
            if record is not None:
                if record["request"] != request:
                    raise ValueError("User prompt hook request mismatch")
                if record["result"] is None and not owner._async.owns(key, request):
                    raise RuntimeError("User prompt hook outcome unknown; not repeated")
            elif current.get(command.key) != command:
                raise RuntimeError("User prompt hook authorization changed; not resumed")
    for warning in warnings:
        await runtime.events.emit(WarningEvent(thread, turn, warning))

    async def execute(command, key, request):
        try:
            if isinstance(command, MCPHook):
                from corki.core.mcp_tool_hooks import run

                result = await run(command, payload, mcp_manager, thread)
            else:
                result = await run_command(
                    command,
                    payload,
                    shell=shell,
                    cwd=settings.working_directory,
                    environment={**owner.environment, **dict(command.environment)},
                )
        except (OSError, ValueError, TimeoutError) as error:
            result = {"error": f"UserPromptSubmit failed: {error}"}
        await repository.complete_hook_execution(thread, turn, key, request, result)
        return result

    async def context_for(command, key, parsed):
        if parsed.context and parsed.context.strip():
            context = await prepare_context(
                repository, state, key, parsed.context, command.additional_context_limit
            )
            return replace(context, source_input_id=item.id)
        return None

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
                # Durable results are consumed at prepare/finalize, including
                # after a cold restart. Never mutate history from this task.
                return "", ()

            if result is None:
                owner._async.start(key, request, background)
            else:
                owner._async.publish(key, await background())
            return None
        return result if result is not None else await execute(command, key, request)

    for command in commands:
        if not command.asynchronous:
            await runtime.events.emit(
                HookStarted(
                    thread,
                    turn,
                    HookRunSummary(
                        prefix + command.key,
                        command.key,
                        "UserPromptSubmit",
                        "running",
                        command.status_message,
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
    contexts = []
    for command, result in zip(commands, results, strict=True):
        if command.asynchronous:
            continue
        parsed = outcome(result)
        stopped |= parsed.stopped
        context = await context_for(command, prefix + command.key, parsed)
        if context is not None:
            contexts.append(context)
        entries = parsed.entries + (
            (HookOutputEntry("context", context.content),) if context is not None else ()
        )
        await runtime.events.emit(
            HookCompleted(
                thread,
                turn,
                HookRunSummary(
                    prefix + command.key,
                    command.key,
                    "UserPromptSubmit",
                    parsed.status,
                    command.status_message,
                    entries,
                ),
            )
        )
    if stopped and contexts:
        await repository.append_items(thread, tuple(contexts))
    await repository.save_hook_batch(
        thread,
        turn,
        receipt_key(item),
        {
            "version": 1,
            "prompt": item.content,
            "stopped": stopped,
            "contexts": [item_to_payload(context) for context in contexts],
        },
    )
    return stopped
