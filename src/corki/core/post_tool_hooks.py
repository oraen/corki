"""Post feedback is projected after immutable tool execution facts are committed."""

import asyncio
import json
import logging
from dataclasses import asdict, replace

from corki.core.hook_context import prepare_context
from corki.core.hook_matchers import matches
from corki.core.stop_hooks import MCPHook, _execution_request, restore_command, run_command
from corki.mcp.arguments import hook_arguments
from corki.mcp.tools import MCPTool
from corki.protocol.events import (
    HookCompleted,
    HookOutputEntry,
    HookRunSummary,
    HookStarted,
    WarningEvent,
)
from corki.protocol.items import UserMessageItem
from corki.protocol.session_source import SessionSourceKind, SubAgentSource
from corki.protocol.tools import ToolCall
from corki.protocol.wire_numbers import loads_number_values
from corki.tools.builtin.patch import ApplyPatchTool
from corki.tools.builtin.shell import ExecCommandTool, WriteStdinTool


def outcome(result, *, control=True):
    """block, model feedback, additional context, warning, diagnostic."""
    if result.get("error"):
        return False, None, None, None, str(result["error"])
    code = result.get("exit_code")
    if code == 2 and control:
        reason = result.get("stderr", "").strip()
        return (
            (True, reason, None, None, None)
            if reason
            else (False, None, None, None, "PostToolUse exit 2 requires stderr feedback")
        )
    if code != 0:
        return False, None, None, None, f"PostToolUse exited with status {code}"
    raw = result.get("stdout", "").strip()
    if not raw or not raw.startswith(("{", "[")):
        return False, None, None, None, None
    warning = None
    try:
        value = json.loads(raw)
        json.dumps(value, allow_nan=False, ensure_ascii=False).encode("utf-8")
        if not isinstance(value, dict):
            raise ValueError("PostToolUse output must be an object")
        if set(value) - {
            "continue",
            "stopReason",
            "suppressOutput",
            "systemMessage",
            "decision",
            "reason",
            "hookSpecificOutput",
        }:
            raise ValueError("Unknown PostToolUse output field")
        if value.get("systemMessage") is not None and not isinstance(value["systemMessage"], str):
            raise ValueError("systemMessage must be a string")
        specific = value.get("hookSpecificOutput")
        if specific is None:
            specific = {}
        if not isinstance(specific, dict):
            raise ValueError("hookSpecificOutput must be an object")
        if value.get("hookSpecificOutput") is not None:
            event = specific.get("hookEventName")
            if not isinstance(event, str) or event not in {
                "PreToolUse",
                "PermissionRequest",
                "PostToolUse",
                "PreCompact",
                "PostCompact",
                "SessionStart",
                "UserPromptSubmit",
                "SubagentStart",
                "SubagentStop",
                "Stop",
                "Interrupt",
            }:
                raise ValueError("Invalid or missing Hook event")
            if set(specific) - {"hookEventName", "additionalContext", "updatedMCPToolOutput"}:
                raise ValueError("Unknown PostToolUse specific output field")
        if not isinstance(value.get("suppressOutput", False), bool):
            raise ValueError("suppressOutput must be boolean")
        continuing = value.get("continue", True)
        if not isinstance(continuing, bool):
            raise ValueError("continue must be boolean")
        reason, stop = value.get("reason"), value.get("stopReason")
        context = specific.get("additionalContext")
        if any(item is not None and not isinstance(item, str) for item in (reason, stop, context)):
            raise ValueError("PostToolUse feedback must be a string")
        decision = value.get("decision")
        if decision not in (None, "block"):
            raise ValueError("Unsupported PostToolUse decision")
        warning = value.get("systemMessage")
        if not control:
            return False, None, context, warning, None
        invalid = None
        if value.get("suppressOutput") or specific.get("updatedMCPToolOutput") is not None:
            invalid = "Unsupported PostToolUse output control"
        if decision == "block" and (not reason or not reason.strip()):
            invalid = invalid or "PostToolUse block requires a reason"
        if continuing and decision is None and reason is not None:
            invalid = invalid or "PostToolUse reason requires a decision"
        if not continuing:
            return (
                False,
                (reason or "").strip()
                or (stop if stop is not None else "PostToolUse hook stopped execution"),
                context if invalid is None else None,
                warning,
                None,
            )
        if invalid:
            raise ValueError(invalid)
        if decision == "block":
            if not reason or not reason.strip():
                raise ValueError("PostToolUse block requires a reason")
            return True, reason, context, warning, None
        if reason is not None:
            raise ValueError("PostToolUse reason requires a decision")
        return False, None, context, warning, None
    except (ValueError, TypeError, RecursionError) as error:
        return False, None, None, warning, str(error)


def payload_for(result, tool):
    if result.is_error or result.dispatch_error:
        return None
    if isinstance(tool, (ExecCommandTool, WriteStdinTool)):
        return loads_number_values(result.post_tool_use_json) if result.post_tool_use_json else None
    if result.execution_input_json is None:
        return None
    executed = loads_number_values(result.execution_input_json)
    if executed["input_kind"] != "json":
        return None
    arguments = executed["arguments"]
    name, response = result.tool_name, result.content
    if isinstance(tool, MCPTool):
        name = tool.hook_tool_name
        arguments = hook_arguments(
            ToolCall(
                result.call_id, result.tool_name, arguments, raw_arguments=executed["raw_arguments"]
            )
        )
        if result.mcp_result_json is None:
            return None
        response = loads_number_values(result.mcp_result_json)
    elif isinstance(tool, ApplyPatchTool):
        name, arguments = "apply_patch", {"command": arguments["patch"]}
    return {
        "tool_name": name,
        "tool_use_id": str(result.call_id),
        "tool_input": arguments,
        "tool_response": response,
    }


async def run(
    snapshot,
    *,
    result,
    tool,
    state,
    runtime,
    settings,
    repository,
    shell,
    environment,
    fresh,
    nested,
    prepare_only=False,
    async_owner=None,
    mcp_manager=None,
):
    if fresh and not any(snapshot):
        return None if prepare_only else result
    batch_key = f"post_tool_use:{state['thread_id']}:{state['turn_id']}:{result.call_id}:"
    saved = await repository.load_hook_batch(state["thread_id"], state["turn_id"], batch_key)
    if saved is None:
        if not fresh:
            return None if prepare_only else result
        source = runtime.session_source
        child = (
            source.kind == SessionSourceKind.SUBAGENT
            and isinstance(source.value, SubAgentSource)
            and source.value.variant == "thread_spawn"
        )
        if (source.is_non_root_agent or runtime.is_non_root_agent) and not child:
            return None if prepare_only else result
        payload = payload_for(result, tool)
        if payload is None:
            return None if prepare_only else result
        payload.pop("version", None)
        names = [payload["tool_name"]]
        if isinstance(tool, ApplyPatchTool):
            names.extend(("Write", "Edit"))
        elif payload["tool_name"] == "spawn_agent":
            names.append("Agent")
        commands, warnings = snapshot
        selected = []
        for warning in warnings:
            await runtime.events.emit(WarningEvent(state["thread_id"], state["turn_id"], warning))
        for command in commands:
            if not any(matches(command.matcher, name) for name in names):
                continue
            selected.append(command)
        if not selected:
            return None if prepare_only else result
        transcript = None
        try:
            path = await repository.materialize_transcript(state["thread_id"])
            transcript = str(path) if path is not None else None
        except OSError:
            logging.getLogger(__name__).warning(
                "Could not materialize Post transcript", exc_info=True
            )
        payload.update(
            session_id=str(await repository.load_thread_session_id(state["thread_id"])),
            turn_id=str(state["turn_id"]),
            cwd=str(settings.working_directory),
            hook_event_name="PostToolUse",
            model=settings.model,
            transcript_path=transcript,
            permission_mode="bypassPermissions"
            if settings.execution_permissions is not None
            and settings.execution_permissions.approval_policy_json == '"never"'
            else "default",
        )
        if child:
            role = source.value.value.agent_role
            payload.update(
                agent_id=str(state["thread_id"]), agent_type=role if role is not None else "default"
            )
        source_id = next(
            (
                str(item.id)
                for item in reversed(state["request_items"])
                if isinstance(item, UserMessageItem) and item.turn_id == state["turn_id"]
            ),
            None,
        )
        batch = {
            "payload": payload,
            "commands": [asdict(command) for command in selected],
            "source_input_id": source_id,
        }
        if nested:
            from corki.code_mode.ownership import parent_call_id

            batch["parent_call_id"] = parent_call_id.get()
        if not prepare_only:
            await repository.save_hook_batch(state["thread_id"], state["turn_id"], batch_key, batch)
        records = {}
    else:
        batch, records = saved
        payload = batch["payload"]
        selected = [restore_command(item) for item in batch["commands"]]

    if prepare_only:
        return batch_key, batch

    async def execute(index, command):
        key = batch_key + str(index)
        if command.asynchronous:
            if async_owner is None:
                raise RuntimeError("Asynchronous PostToolUse requires a session owner")
            await async_owner.schedule(
                key,
                command,
                payload,
                batch["source_input_id"],
                repository=repository,
                shell=shell,
                environment=environment,
                fresh=fresh,
                records=records,
                thread=state["thread_id"],
                turn=state["turn_id"],
            )
            return None
        if not fresh:
            record = records.get(key)
            return key, command, record["result"] if record else None
        request = _execution_request(command, payload)
        await runtime.events.emit(
            HookStarted(
                state["thread_id"],
                state["turn_id"],
                HookRunSummary(key, command.key, "PostToolUse", "running", command.status_message),
            )
        )
        raw = await repository.claim_hook_execution(
            state["thread_id"], state["turn_id"], key, request
        )
        if raw is None:
            try:
                if isinstance(command, MCPHook):
                    from corki.core.mcp_tool_hooks import run as run_mcp

                    raw = await run_mcp(command, payload, mcp_manager, state["thread_id"])
                else:
                    raw = await run_command(
                        command,
                        payload,
                        shell=shell,
                        cwd=settings.working_directory,
                        environment={**environment, **dict(command.environment)},
                    )
            except (OSError, ValueError, TimeoutError) as error:
                raw = {"error": f"PostToolUse failed: {type(error).__name__}: {error}"}
            await repository.complete_hook_execution(
                state["thread_id"], state["turn_id"], key, request, raw
            )
        return key, command, raw

    tasks = [asyncio.create_task(execute(index, command)) for index, command in enumerate(selected)]
    try:
        completed = await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
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
    block, feedback, contexts = False, [], []
    for completion in completed:
        if completion is None:
            continue
        key, command, raw = completion
        if raw is None:
            blocked, text, context, warning, error = (
                True,
                "PostToolUse outcome unknown; not repeated.",
                None,
                None,
                None,
            )
        else:
            blocked, text, context, warning, error = outcome(raw)
        block |= blocked
        if text is not None:
            feedback.append(text)
        entries = []
        for kind, message in (("error", error), ("warning", warning), ("feedback", text)):
            if kind == "feedback" and text is not None and not blocked:
                kind = "stop"
                stop = json.loads(raw["stdout"]).get("stopReason")
                message = stop if stop is not None else "PostToolUse hook stopped execution"
            if message is not None:
                entries.append(HookOutputEntry(kind, message[:4000]))
        if context:
            fragment = await prepare_context(
                repository, state, key, context, command.additional_context_limit
            )
            fragment = replace(fragment, source_input_id=batch["source_input_id"])
            contexts.append(fragment)
            entries.append(HookOutputEntry("context", fragment.content))
        if fresh:
            await runtime.events.emit(
                HookCompleted(
                    state["thread_id"],
                    state["turn_id"],
                    HookRunSummary(
                        key,
                        command.key,
                        "PostToolUse",
                        "blocked"
                        if blocked
                        else "failed"
                        if error
                        else "stopped"
                        if text is not None
                        else "completed",
                        command.status_message,
                        tuple(entries),
                    ),
                )
            )
    if contexts:
        await repository.append_items(state["thread_id"], tuple(contexts))
    if block or (feedback and not nested):
        return replace(
            result,
            content="\n\n".join(feedback),
            is_error=block,
            dispatch_error=block,
            attachments=(),
            content_items=(),
            discovered_tools=() if block else result.discovered_tools,
        )
    return result
