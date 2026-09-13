"""Trusted synchronous command hooks at the ordinary tool admission boundary."""

import asyncio
import json
import logging
from dataclasses import asdict, replace

from corki.code_mode.tools import CodeModeWaitTool
from corki.core.hook_context import prepare_context
from corki.core.hook_matchers import matches
from corki.core.stop_hooks import MCPHook, _execution_request, run_command
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
from corki.tools.builtin.patch import ApplyPatchTool
from corki.tools.builtin.shell import ExecCommandTool, WriteStdinTool
from corki.tools.executor import _validate


def outcome(result):
    """Return block/rewrite/error/warning; a systemMessage is not model context."""
    if result.get("error"):
        return None, None, str(result["error"]), None, None
    code = result.get("exit_code")
    if code == 2:
        reason = result.get("stderr", "").strip()
        return (
            (reason, None, None, None, None)
            if reason
            else (None, None, "PreToolUse exit 2 requires a reason", None, None)
        )
    if code != 0:
        return None, None, f"PreToolUse hook exited with code {code}", None, None
    text = result.get("stdout", "").strip()
    if not text.startswith(("{", "[")):
        return None, None, None, None, None
    warning = None
    try:
        value = json.loads(text)
        json.dumps(value, allow_nan=False, ensure_ascii=False).encode("utf-8")
        if not isinstance(value, dict):
            raise ValueError("expected an object")
        specific = value.get("hookSpecificOutput")
        if specific is None:
            specific = {}
        if not isinstance(specific, dict):
            raise ValueError("invalid hookSpecificOutput")
        if specific.get("hookEventName", "PreToolUse") != "PreToolUse":
            raise ValueError("wrong hook event")
        context = specific.get("additionalContext")
        if context is not None and not isinstance(context, str):
            raise ValueError("additionalContext must be a string")
        if value.get("systemMessage") is not None:
            if not isinstance(value["systemMessage"], str):
                raise ValueError("systemMessage must be a string")
            warning = value["systemMessage"][:4000]
        if (
            value.get("continue", True) is not True
            or value.get("stopReason") is not None
            or value.get("suppressOutput", False) is not False
        ):
            raise ValueError("unsupported PreToolUse control output")
        decision = specific.get("permissionDecision")
        if specific.get("updatedInput") is not None and decision != "allow":
            raise ValueError("updatedInput requires permissionDecision:allow")
        if (
            decision is not None
            or specific.get("permissionDecisionReason") is not None
            or specific.get("updatedInput") is not None
        ):
            if decision == "allow" and isinstance(specific.get("updatedInput"), dict):
                return None, specific["updatedInput"], None, warning, context
            if decision != "deny":
                raise ValueError("unsupported PreToolUse permission decision or updatedInput")
            reason = specific.get("permissionDecisionReason")
        else:
            decision = value.get("decision")
            if decision is None and value.get("reason") is None:
                return None, None, None, warning, context
            if decision != "block":
                raise ValueError("unsupported PreToolUse decision")
            reason = value.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("PreToolUse block requires a non-empty reason")
        return reason.strip(), None, None, warning, context
    except (ValueError, RecursionError) as error:
        return None, None, f"Invalid PreToolUse output: {error}", warning, None


def bind(
    snapshot,
    *,
    state,
    runtime,
    settings,
    repository,
    shell,
    environment,
    async_owner=None,
    mcp_manager=None,
):
    """Capture trusted definitions before the tool's first asynchronous boundary."""
    commands, warnings = snapshot

    async def before(call, tool):
        source = runtime.session_source
        child = (
            source.kind == SessionSourceKind.SUBAGENT
            and isinstance(source.value, SubAgentSource)
            and source.value.variant == "thread_spawn"
        )
        if (source.is_non_root_agent or runtime.is_non_root_agent) and not child:
            return call
        if isinstance(tool, (CodeModeWaitTool, WriteStdinTool)) or call.input_kind != "json":
            return call
        patch_tool = isinstance(tool, ApplyPatchTool)
        shell_tool = isinstance(tool, ExecCommandTool)
        command_field = "patch" if patch_tool else "cmd" if shell_tool else None
        if command_field is not None and (
            call.arguments is None or not isinstance(call.arguments.get(command_field), str)
        ):
            return call
        if shell_tool:
            if call.parse_error is not None:
                return call
            # Native ExecCommandArgs is parsed before Hook admission. Workdir is
            # resolved separately afterwards; Option fields also accept null.
            for name, schema in tool.spec.parameters["properties"].items():
                if name == "workdir" or name not in call.arguments:
                    continue
                value = call.arguments[name]
                if value is None and name not in {"cmd", "tty", "yield_time_ms"}:
                    continue
                try:
                    _validate(value, schema, path=f"arguments.{name}")
                except ValueError:
                    return call
        for warning in warnings:
            await runtime.events.emit(WarningEvent(state["thread_id"], state["turn_id"], warning))
        # Compatibility aliases select handlers; they never rename the call or payload.
        hook_name = tool.hook_tool_name if isinstance(tool, MCPTool) else call.name
        matcher_names = (hook_name, "Agent") if hook_name == "spawn_agent" else (hook_name,)
        if patch_tool:
            matcher_names = ("apply_patch", "Write", "Edit")
        elif shell_tool:
            matcher_names = ("Bash",)
        selected = tuple(
            command
            for command in commands
            if any(matches(command.matcher, name) for name in matcher_names)
        )
        synchronous = [command for command in selected if not command.asynchronous]
        if not selected:
            return call
        transcript_path = None
        materialize = getattr(repository, "materialize_transcript", None)
        if materialize is not None:
            try:
                path = await materialize(state["thread_id"])
                transcript_path = str(path) if path is not None else None
            except OSError:
                logging.getLogger(__name__).warning(
                    "Could not materialize PreToolUse transcript", exc_info=True
                )
        payload = {
            "session_id": str(await repository.load_thread_session_id(state["thread_id"])),
            "turn_id": str(state["turn_id"]),
            "cwd": str(settings.working_directory),
            "hook_event_name": "PreToolUse",
            "model": settings.model,
            "permission_mode": "bypassPermissions"
            if settings.execution_permissions is not None
            and settings.execution_permissions.approval_policy_json == '"never"'
            else "default",
            "transcript_path": transcript_path,
            "tool_name": "apply_patch" if patch_tool else "Bash" if shell_tool else hook_name,
            "tool_use_id": str(call.id),
            "tool_input": {"command": call.arguments[command_field]}
            if command_field is not None
            else hook_arguments(call)
            if isinstance(tool, MCPTool)
            else call.arguments,
        }
        if child:
            role = source.value.value.agent_role
            payload.update(
                agent_id=str(state["thread_id"]),
                agent_type=role if role is not None else "default",
            )
        completed_order = []

        source_input_id = next(
            (
                item.id
                for item in reversed(state["request_items"])
                if isinstance(item, UserMessageItem) and item.turn_id == state["turn_id"]
            ),
            None,
        )

        asynchronous = [command for command in selected if command.asynchronous]
        if asynchronous:
            if async_owner is None:
                raise RuntimeError("Asynchronous PreToolUse requires a session owner")
            prefix = f"pre_tool_use:{state['thread_id']}:{state['turn_id']}:{call.id}:"
            saved = await repository.load_hook_batch(state["thread_id"], state["turn_id"], prefix)
            if saved is None:
                batch = {
                    "payload": payload,
                    "commands": [asdict(c) for c in asynchronous],
                    "source_input_id": source_input_id,
                }
                await repository.save_hook_batch(
                    state["thread_id"], state["turn_id"], prefix, batch
                )
                # Existing batches belong to their original snapshot. Recovery
                # publishes known results; never rerun unknown or unclaimed effects.
                for index, command in enumerate(asynchronous):
                    await async_owner.schedule(
                        prefix + str(index),
                        command,
                        payload,
                        source_input_id,
                        repository=repository,
                        shell=shell,
                        environment=environment,
                        fresh=True,
                        records={},
                        thread=state["thread_id"],
                        turn=state["turn_id"],
                    )

        async def execute(order, command):
            key = f"pre_tool_use:{state['turn_id']}:{call.id}:{command.key}"
            request = _execution_request(command, payload)
            request["feedback"] = {
                "version": 1,
                "order": order,
                "limit": command.additional_context_limit,
                "source_input_id": source_input_id,
            }
            await runtime.events.emit(
                HookStarted(
                    state["thread_id"],
                    state["turn_id"],
                    HookRunSummary(
                        key, command.key, "PreToolUse", "running", command.status_message
                    ),
                )
            )
            result = await repository.claim_hook_execution(
                state["thread_id"], state["turn_id"], key, request
            )
            fresh = result is None
            if result is None:
                try:
                    if isinstance(command, MCPHook):
                        from corki.core.mcp_tool_hooks import run as run_mcp

                        result = await run_mcp(command, payload, mcp_manager, state["thread_id"])
                    else:
                        result = await run_command(
                            command,
                            payload,
                            shell=shell,
                            cwd=settings.working_directory,
                            environment={**environment, **dict(command.environment)},
                        )
                except (OSError, ValueError, TimeoutError) as error:
                    result = {"error": f"PreToolUse hook failed: {type(error).__name__}: {error}"}
            parsed = outcome(result)
            completed_order.append(parsed)
            if fresh:
                await repository.complete_hook_execution(
                    state["thread_id"], state["turn_id"], key, request, result
                )
            return key, command, parsed

        tasks = [
            asyncio.create_task(execute(order, command))
            for order, command in enumerate(synchronous)
        ]
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
            joined.result()
            if cancelled:
                raise asyncio.CancelledError
        block = None
        contexts = []
        for key, command, (reason, _, diagnostic, warning, context) in results:
            block = block or reason
            status = "blocked" if reason else "failed" if diagnostic else "completed"
            entries = (
                (HookOutputEntry("feedback" if reason else "error", (reason or diagnostic)[:4000]),)
                if reason or diagnostic
                else ()
            )
            if warning is not None:
                entries = (HookOutputEntry("warning", warning), *entries)
            if context is not None:
                item = await prepare_context(
                    repository, state, key, context, command.additional_context_limit
                )
                contexts.append(item)
                entries = (*entries, HookOutputEntry("context", item.content))
            await runtime.events.emit(
                HookCompleted(
                    state["thread_id"],
                    state["turn_id"],
                    HookRunSummary(
                        key, command.key, "PreToolUse", status, command.status_message, entries
                    ),
                )
            )
        if contexts:
            await repository.append_items(state["thread_id"], tuple(contexts))
        if block:
            return "Tool call blocked by PreToolUse hook: " + block[:4000]
        updated = next(
            (value for _, value, _, _, _ in reversed(completed_order) if value is not None), None
        )
        if command_field is not None and updated is not None:
            if not isinstance(updated.get("command"), str):
                return "hook returned updatedInput without string field `command`"
            updated = (
                {"patch": updated["command"]}
                if patch_tool
                else {**call.arguments, "cmd": updated["command"]}
            )
        return (
            call
            if updated is None
            else replace(
                call,
                arguments=updated,
                raw_arguments=json.dumps(updated, allow_nan=False),
                parse_error=None,
            )
        )

    return before
