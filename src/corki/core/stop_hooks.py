"""Trusted Stop/SubagentStop commands with separate approval and execution identity."""

import asyncio
import json
import logging
import os
import signal
import tomllib
from contextlib import suppress
from dataclasses import asdict, dataclass
from enum import StrEnum
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5

from corki.core import hook_matchers
from corki.core.async_hooks import AsyncHooks
from corki.protocol.events import (
    HookCompleted,
    HookOutputEntry,
    HookRunSummary,
    HookStarted,
    WarningEvent,
)
from corki.protocol.ids import ItemId
from corki.protocol.items import AssistantMessageItem, ContextItem, ContextRole, UserMessageItem
from corki.protocol.session_source import SessionSourceKind, SubAgentSource, ThreadSpawnSource
from corki.tools.builtin.shell_environment import _NON_INHERITABLE

_LOG = logging.getLogger(__name__)


class StopDecision(StrEnum):
    ALLOW = "allow"
    BLOCK = "block"
    STOP = "stop"


def command_identity(handler, *, event_name="Stop", matcher=None):
    """Normalize the supported Stop command before canonical config hashing."""
    if event_name == "Interrupt":
        matcher = None
    if handler.get("type") == "mcp_tool":
        if event_name == "SessionEnd":
            raise ValueError("SessionEnd MCP hooks are not supported")
        from corki.core.mcp_tool_hooks import normalize

        normalized = normalize(handler)
        if event_name == "Interrupt":
            normalized["timeout"] = (
                min(3, normalized["timeout"]) if handler.get("timeout") is not None else 1
            )
        events = {
            "Stop": "stop",
            "SubagentStop": "subagent_stop",
            "PreToolUse": "pre_tool_use",
            "PostToolUse": "post_tool_use",
            "PreCompact": "pre_compact",
            "PostCompact": "post_compact",
            "UserPromptSubmit": "user_prompt_submit",
            "SessionStart": "session_start",
            "SubagentStart": "subagent_start",
            "Interrupt": "interrupt",
        }
        if event_name not in events:
            raise ValueError("Unsupported MCP hook event")
        identity = {"event_name": events[event_name], "hooks": [normalized]}
        if event_name != "Stop":
            hook_matchers.validate(matcher)
            if matcher is not None:
                identity["matcher"] = matcher
        encoded = json.dumps(
            identity, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        return "sha256:" + sha256(encoded.encode()).hexdigest(), normalized
    if handler.get("type") != "command" or not isinstance(handler.get("command"), str):
        raise ValueError("Stop hook requires a command string")
    timeout = handler.get("timeout")
    bounded = event_name in {"SessionEnd", "Interrupt"}
    timeout = (1 if bounded else 600) if timeout is None else timeout
    if type(timeout) is not int or not 0 <= timeout < 2**64:
        raise ValueError("Stop hook timeout must be an unsigned integer")
    if type(handler.get("async", False)) is not bool:
        raise ValueError("Stop hook async must be a boolean")
    for field in ("statusMessage", "commandWindows", "command_windows"):
        if handler.get(field) is not None and not isinstance(handler[field], str):
            raise ValueError(f"Stop hook {field} must be a string")
    if "commandWindows" in handler and "command_windows" in handler:
        raise ValueError("duplicate commandWindows field")
    command = handler["command"]
    windows_command = handler.get("commandWindows", handler.get("command_windows"))
    if os.name == "nt" and windows_command is not None:
        command = windows_command
    if not command.strip():
        raise ValueError("Stop hook command is empty")
    limit = handler.get("additionalContextLimit")
    if limit is not None and (type(limit) is not int or not 0 <= limit < 2**64):
        raise ValueError("Stop hook additionalContextLimit must be an unsigned integer")
    # Stop does not consume additionalContextLimit. Unknown fields disappear
    # during native typed deserialization; None fields disappear at the TOML step.
    normalized = {
        "type": "command",
        "command": command,
        "timeout": min(3, max(1, timeout)) if bounded else max(1, timeout),
        "async": handler.get("async", False),
    }
    if handler.get("statusMessage") is not None:
        normalized["statusMessage"] = handler["statusMessage"]
    event_keys = {
        "Stop": "stop",
        "SubagentStop": "subagent_stop",
        "PreToolUse": "pre_tool_use",
        "PostToolUse": "post_tool_use",
        "PreCompact": "pre_compact",
        "PostCompact": "post_compact",
        "UserPromptSubmit": "user_prompt_submit",
        "SessionStart": "session_start",
        "SubagentStart": "subagent_start",
        "SessionEnd": "session_end",
        "Interrupt": "interrupt",
    }
    if event_name not in event_keys:
        raise ValueError("Unsupported stop hook event")
    if (
        event_name
        in {"PreToolUse", "PostToolUse", "UserPromptSubmit", "SessionStart", "SubagentStart"}
        and limit is not None
    ):
        normalized["additionalContextLimit"] = limit
    identity = {
        "event_name": event_keys[event_name],
        "hooks": [normalized],
    }
    if event_name != "Stop":
        hook_matchers.validate(matcher)
        if matcher is not None:
            identity["matcher"] = matcher
    encoded = json.dumps(
        identity,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    return "sha256:" + sha256(encoded.encode()).hexdigest(), normalized


@dataclass(frozen=True)
class StopCommand:
    key: str
    fingerprint: str
    command: str
    timeout: int
    environment: tuple[tuple[str, str], ...] = ()
    status_message: str | None = None
    matcher: str | None = None
    asynchronous: bool = False
    additional_context_limit: int = 2500


@dataclass(frozen=True)
class MCPHook(StopCommand):
    mcp: dict | None = None


def restore_command(entry):
    return (MCPHook if "mcp" in entry else StopCommand)(**entry)


def discover(configuration, plugins=(), *, event_name="Stop", managed_policy=None):
    event_key = {
        "Stop": "stop",
        "SubagentStop": "subagent_stop",
        "PreToolUse": "pre_tool_use",
        "PostToolUse": "post_tool_use",
        "PreCompact": "pre_compact",
        "PostCompact": "post_compact",
        "UserPromptSubmit": "user_prompt_submit",
        "SessionStart": "session_start",
        "SubagentStart": "subagent_start",
        "SessionEnd": "session_end",
        "Interrupt": "interrupt",
    }[event_name]
    commands, warnings, states, layers = [], [], {}, []
    visited_json_folders = set()
    for layer in configuration.layers if configuration is not None else ():
        if layer.disabled_reason is not None and layer.kind != "user":
            continue
        document = tomllib.loads(layer.contents)
        hooks = document.get("hooks", {})
        if not isinstance(hooks, dict):
            warnings.append(f"Invalid hooks table in {layer.file}")
            hooks = {}
        if layer.disabled_reason is None:
            folder = layer.file.parent
            if layer.hooks_json is not None and folder not in visited_json_folders:
                visited_json_folders.add(folder)
                source = folder / "hooks.json"
                try:
                    json_document = json.loads(layer.hooks_json)
                    if not isinstance(json_document, dict) or set(json_document) - {
                        "hooks",
                        "description",
                    }:
                        raise ValueError("hook file must contain only hooks and description")
                    if json_document.get("description") is not None and not isinstance(
                        json_document["description"], str
                    ):
                        raise ValueError("description must be a string")
                    json_hooks = (
                        json_document.get("hooks", {}) if isinstance(json_document, dict) else None
                    )
                    if not isinstance(json_hooks, dict):
                        raise ValueError("hooks must be an object")
                    layers.append((source, json_hooks, ()))
                    if json_hooks.get(event_name) and hooks.get(event_name):
                        warnings.append(
                            f"Loading hooks from both {source} and {layer.file}; "
                            "prefer a single representation"
                        )
                except ValueError as error:
                    warnings.append(f"Failed to parse hooks JSON in {source}: {error}")
            layers.append((layer.file, hooks, ()))
        # Never consume merged project/plugin state as user authority.
        if layer.kind == "user" and isinstance(hooks.get("state", {}), dict):
            for raw_key, value in hooks.get("state", {}).items():
                key = raw_key.strip()
                if not key:
                    continue
                # Remember explicit canonical entries even if malformed so a
                # legacy alias cannot silently supply authority in their place.
                effective = states.setdefault(key, {})
                if not isinstance(value, dict) or any(
                    field in value and type(value[field]) is not expected
                    for field, expected in (("enabled", bool), ("trusted_hash", str))
                ):
                    continue
                # Disabled definition layers still carry user preferences.
                # A hash-only update must not erase an earlier enabled=false.
                for field in ("enabled", "trusted_hash"):
                    if field in value:
                        effective[field] = value[field]
    for plugin in plugins:
        manifest = plugin.manifest
        if not manifest.enabled or manifest.error is not None or manifest.agent_plugin:
            continue
        for source in manifest.hook_sources:
            environment = (
                ("PLUGIN_ROOT", str(manifest.root)),
                ("CLAUDE_PLUGIN_ROOT", str(manifest.root)),
                ("PLUGIN_DATA", str(source.data_root)),
                ("CLAUDE_PLUGIN_DATA", str(source.data_root)),
            )
            layers.append(
                (
                    f"{manifest.identity}:{source.relative_path}",
                    json.loads(source.contents).get("hooks", {}),
                    environment,
                )
            )
    sources = [(source, hooks, environment, False) for source, hooks, environment in layers]
    if managed_policy is not None:
        if managed_policy.only_managed:
            sources = []
            warnings.clear()
        sources.insert(0, (managed_policy.source, json.loads(managed_policy.hooks_json), (), True))
    for source, hooks, environment, managed in sources:
        groups = hooks.get(event_name, [])
        if not isinstance(groups, list):
            warnings.append(f"Invalid Stop hook groups in {source}")
            continue
        for group_index, group in enumerate(groups):
            if not isinstance(group, dict) or not isinstance(group.get("hooks", []), list):
                warnings.append(f"Invalid Stop hook group in {source}")
                continue
            for index, handler in enumerate(group.get("hooks", [])):
                key = f"{source}:{event_key}:{group_index}:{index}"
                try:
                    if not isinstance(handler, dict):
                        raise ValueError("Stop hook must be an object")
                    fingerprint, normalized = command_identity(
                        handler, event_name=event_name, matcher=group.get("matcher")
                    )
                    if event_name == "SessionEnd":
                        if handler.get("async", False):
                            warnings.append(f"Running async SessionEnd hook synchronously: {key}")
                        if handler.get("timeout") is not None and handler["timeout"] > 3:
                            warnings.append(f"Clamping SessionEnd hook timeout to 3s: {key}")
                    if (
                        event_name == "Interrupt"
                        and handler.get("timeout", 0) is not None
                        and handler.get("timeout", 0) > 3
                    ):
                        warnings.append(f"Clamping Interrupt hook timeout to 3s: {key}")
                    # Canonical native config keys take precedence even when
                    # invalid, disabled or untrusted. Only absent keys may use
                    # Corki's former spelling; never resurrect revoked approval.
                    approval = states[key] if key in states else states.get(f"file:{key}", {})
                    if not managed and (
                        not isinstance(approval, dict) or approval.get("enabled", True) is not True
                    ):
                        continue
                    if not managed and approval.get("trusted_hash") != fingerprint:
                        warnings.append(f"Stop hook is untrusted or modified: {key}")
                        continue
                    if normalized["type"] == "mcp_tool":
                        commands.append(
                            MCPHook(
                                key,
                                fingerprint,
                                "",
                                normalized["timeout"],
                                environment,
                                normalized.get("statusMessage"),
                                group.get("matcher")
                                if event_name not in {"Stop", "Interrupt"}
                                else None,
                                mcp=normalized,
                            )
                        )
                        continue
                    command = normalized["command"]
                    for variable, value in environment:
                        command = command.replace("${" + variable + "}", value)
                    commands.append(
                        StopCommand(
                            key,
                            fingerprint,
                            command,
                            normalized["timeout"],
                            environment,
                            normalized.get("statusMessage"),
                            group.get("matcher")
                            if event_name not in {"Stop", "Interrupt"}
                            else None,
                            normalized["async"] if event_name != "SessionEnd" else False,
                            normalized.get("additionalContextLimit", 2500),
                        )
                    )
                except ValueError as error:
                    if managed:
                        raise ValueError(
                            f"failed to load required managed hooks: {key}: {error}"
                        ) from error
                    warnings.append(f"{key}: {error}")
    return tuple(commands), tuple(warnings)


async def run_command(command, payload, *, shell, cwd, environment):
    if os.name != "posix":
        raise ValueError("Stop hook process containment is not implemented on this platform")
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *shell.exec_args(command.command, login=False),
            cwd=cwd,
            env=environment,
            start_new_session=True,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    )
    tasks = []
    completed = False
    try:
        async with asyncio.timeout(command.timeout):
            process = await asyncio.shield(spawn)

            async def read(reader):
                data = bytearray()
                while chunk := await reader.read(8192):
                    data.extend(chunk)
                    if len(data) > 1024 * 1024:
                        raise ValueError("Stop hook output exceeds 1 MiB")
                return data.decode("utf-8", errors="replace")

            async def write():
                try:
                    process.stdin.write(json.dumps(payload).encode())
                    await process.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    process.stdin.close()

            tasks = [
                asyncio.create_task(write()),
                asyncio.create_task(read(process.stdout)),
                asyncio.create_task(read(process.stderr)),
            ]
            _, stdout, stderr = await asyncio.gather(*tasks)
            code = await process.wait()
            completed = True
            return {"exit_code": code, "stdout": stdout, "stderr": stderr}
    finally:

        async def cleanup():
            for task in tasks:
                if not task.done():
                    task.cancel()
            try:
                process = await asyncio.shield(spawn)
            except (OSError, asyncio.CancelledError):
                process = None
            if process is not None:
                if not completed:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    # Cancelled readers may leave full pipe buffers. Closing
                    # transports releases wait() without waiting for a reader
                    # which no longer exists, after terminating the group.
                    process._transport.close()
                await process.wait()
            await asyncio.gather(*tasks, return_exceptions=True)

        owner = asyncio.create_task(cleanup())
        cancelled = False
        while not owner.done():
            try:
                await asyncio.shield(owner)
            except asyncio.CancelledError:
                cancelled = True
        owner.result()
        if cancelled:
            raise asyncio.CancelledError


def outcome(result, *, control=True):
    """Return control, control/error text, and independent user diagnostics."""
    if "error" in result:
        return "allow", result["error"], ()
    code, stdout, stderr = result["exit_code"], result["stdout"], result["stderr"]
    if control and code == 2 and stderr.strip():
        return "block", stderr.strip(), ()
    if code != 0:
        return "allow", f"Stop hook failed with exit status {code}: {stderr.strip()}", ()
    if not stdout.strip():
        return "allow", "", ()
    diagnostics = ()
    try:
        value = json.loads(stdout)
        if not isinstance(value, dict):
            raise ValueError
        for field in ("continue", "suppressOutput"):
            if field in value and type(value[field]) is not bool:
                raise ValueError
        for field in ("reason", "stopReason", "systemMessage"):
            if value.get(field) is not None and not isinstance(value[field], str):
                raise ValueError
        if value.get("decision") not in (None, "block"):
            raise ValueError
        if value.get("systemMessage") is not None:
            diagnostics = (value["systemMessage"],)
        if not control:
            return "allow", "", diagnostics
        if value.get("continue") is False:
            return "stop", value.get("stopReason") or "", diagnostics
        if value.get("decision") == "block":
            if not (value.get("reason") or "").strip():
                raise ValueError
            return "block", value["reason"].strip(), diagnostics
        return "allow", "", diagnostics
    except (ValueError, TypeError):
        if not control and not stdout.lstrip().startswith(("{", "[")):
            return "allow", "", ()
        return "allow", "Stop hook returned invalid Stop JSON output", diagnostics


def _execution_request(command, payload):
    request = {
        "fingerprint": command.fingerprint,
        "command": command.command,
        "payload": payload,
    }
    if isinstance(command, MCPHook):
        request.pop("command")
        request["mcp"] = command.mcp
    if command.environment:
        request["environment"] = dict(command.environment)
    if command.asynchronous:
        request["async"] = True
    return request


class StopHooks:
    def __init__(self):
        self._snapshot = None
        self._async = AsyncHooks()
        self.start_source = None
        self.environment = {
            k: v for k, v in os.environ.items() if k.lower() not in _NON_INHERITABLE
        }

    @staticmethod
    def prepare(configuration, plugins=(), *, managed_policy=None, enabled=True):
        return {
            event: discover(configuration, plugins, event_name=event, managed_policy=managed_policy)
            if enabled
            else ((), ())
            for event in (
                "Stop",
                "SubagentStop",
                "PreToolUse",
                "PostToolUse",
                "PreCompact",
                "PostCompact",
                "UserPromptSubmit",
                "SessionStart",
                "SubagentStart",
                "SessionEnd",
                "Interrupt",
            )
        }

    def publish(self, snapshot):
        # One assignment publishes definitions and their user authority together.
        # Existing run() invocations retain their captured immutable command tuple.
        self._snapshot = snapshot

    async def drain(self, thread_id, turn_id, events):
        while (entry := self._async.take()) is not None:
            key, (error, warnings) = entry
            if error:
                _LOG.warning("Async hook %s: %s", key, error)
            for warning in warnings:
                await events.emit(WarningEvent(thread_id, turn_id, warning))

    async def _start_async(self, command, payload, key, *, state, repository, shell, settings):
        request = _execution_request(command, payload)
        if self._async.owns(key, request):
            return
        result = await repository.claim_hook_execution(
            state["thread_id"], state["turn_id"], key, request
        )
        if result is not None:
            _, error, warnings = outcome(result, control=False)
            self._async.publish(key, (error, warnings))
            return
        thread_id, turn_id = state["thread_id"], state["turn_id"]
        cwd = settings.working_directory
        environment = {**self.environment, **dict(command.environment)}

        async def execute():
            try:
                result = await run_command(
                    command, payload, shell=shell, cwd=cwd, environment=environment
                )
            except (OSError, ValueError, TimeoutError) as error:
                result = {"error": f"Hook failed: {type(error).__name__}: {error}"}
            await repository.complete_hook_execution(thread_id, turn_id, key, request, result)
            # The ledger retains the raw outcome. The session queue keeps only
            # display diagnostics, never blocked/stopped control or raw output.
            _, error, warnings = outcome(result, control=False)
            return error, warnings

        self._async.start(key, request, execute)

    async def run(
        self, state, runtime, *, settings, repository, shell, plugins=(), mcp_manager=None
    ):
        source = runtime.session_source
        child = (
            source.value.value
            if source.kind == SessionSourceKind.SUBAGENT
            and isinstance(source.value, SubAgentSource)
            and source.value.variant == "thread_spawn"
            else None
        )
        if (source.is_non_root_agent or runtime.is_non_root_agent) and child is None:
            # Synthetic/internal workers do not run user/project/plugin Stop hooks.
            return StopDecision.ALLOW
        event_name = "SubagentStop" if child is not None else "Stop"
        event_key = "subagent_stop" if child is not None else "stop"
        agent_type = (
            child.agent_role if child is not None and child.agent_role is not None else "default"
        )
        commands, warnings = (
            self._snapshot[event_name]
            if self._snapshot is not None
            else discover(settings.configuration, plugins, event_name=event_name)
        )
        if child is not None:
            commands = tuple(
                command
                for command in commands
                if hook_matchers.matches(command.matcher, agent_type)
            )
        prefix = f"{event_key}:{state['turn_id']}:{state['step_count']}:"
        batch = await repository.load_hook_batch(state["thread_id"], state["turn_id"], prefix)
        current_commands = {command.key: command for command in commands}
        if batch is not None:
            snapshot, records = batch
            if snapshot.get("version") not in (1, 2, 3):
                raise RuntimeError("Unsupported hook batch snapshot version")
            commands = tuple(
                restore_command(
                    {**entry, "environment": tuple(tuple(pair) for pair in entry["environment"])}
                )
                for entry in snapshot["commands"]
            )
        elif not commands and not warnings:
            return StopDecision.ALLOW
        for warning in warnings:
            await runtime.events.emit(WarningEvent(state["thread_id"], state["turn_id"], warning))
        history = await repository.load_items(state["thread_id"])
        seen = {i.id for i in state["request_items"] if isinstance(i, UserMessageItem)}
        pending = any(
            isinstance(i, UserMessageItem) and i.turn_id == state["turn_id"] and i.id not in seen
            for i in history
        ) or bool(runtime.realtime.unrecorded_items)
        if runtime.realtime.stop_requested:
            raise asyncio.CancelledError
        # Once admitted, the complete batch survives input and config changes.
        if pending and batch is None:
            return StopDecision.ALLOW
        active = any(
            isinstance(i, ContextItem)
            and i.turn_id == state["turn_id"]
            and i.content_kind == f"hook.{event_key}.feedback"
            and not i.key.startswith(prefix)
            for i in history
        )
        answer = next(
            (
                i.content
                for i in reversed(state["last_model_items"])
                if isinstance(i, AssistantMessageItem) and i.content.strip()
            ),
            None,
        )
        payload = {
            "session_id": str(await repository.load_thread_session_id(state["thread_id"])),
            "turn_id": str(state["turn_id"]),
            "cwd": str(settings.working_directory),
            "hook_event_name": event_name,
            "model": settings.model,
            "stop_hook_active": active,
            "last_assistant_message": answer,
            "transcript_path": None,
            "permission_mode": (
                "bypassPermissions"
                if settings.execution_permissions is not None
                and settings.execution_permissions.approval_policy_json == '"never"'
                else "default"
            ),
        }
        if isinstance(child, ThreadSpawnSource):
            payload.update(
                agent_id=str(state["thread_id"]),
                agent_type=agent_type,
                # New batches populate separate local JSONL paths below.
                # Never mislabel the child's history as the parent transcript.
                transcript_path=None,
                agent_transcript_path=None,
            )
        elif batch is not None and snapshot["version"] == 1:
            # Old root batches used thread identity and omitted these fields.
            # Preserve their immutable request on recovery, never migrate a
            # claimed effect into a new execution identity. New batches use v3.
            payload["session_id"] = str(state["thread_id"])
            del payload["transcript_path"], payload["permission_mode"]
        if batch is not None and snapshot["version"] == 3:
            # Availability is not execution identity. A publication failure (or
            # recovery from one) must not change an admitted command's input.
            payload["transcript_path"] = snapshot["payload"]["transcript_path"]
            if isinstance(child, ThreadSpawnSource):
                payload["agent_transcript_path"] = snapshot["payload"]["agent_transcript_path"]
        if commands and batch is None:
            materialize = getattr(repository, "materialize_transcript", None)
            if materialize is not None:

                async def path_for(thread_id):
                    try:
                        path = await materialize(thread_id)
                        return str(path) if path is not None else None
                    except OSError:
                        _LOG.warning("Could not materialize hook transcript", exc_info=True)
                        return None

                own_path = await path_for(state["thread_id"])
                if isinstance(child, ThreadSpawnSource):
                    payload["agent_transcript_path"] = own_path
                    payload["transcript_path"] = await path_for(child.parent_thread_id)
                else:
                    payload["transcript_path"] = own_path
        source_input_id = next(
            (
                i.id
                for i in reversed(history)
                if isinstance(i, UserMessageItem) and i.turn_id == state["turn_id"]
            ),
            None,
        )
        if batch is None:
            if not commands:
                return StopDecision.ALLOW
            snapshot = {
                "version": 3,
                "commands": [asdict(command) for command in commands],
                "payload": payload,
                "source_input_id": source_input_id,
            }
            await repository.save_hook_batch(state["thread_id"], state["turn_id"], prefix, snapshot)
        else:
            if payload != snapshot["payload"]:
                raise RuntimeError("hook batch request identity collision")
            payload = snapshot["payload"]
            source_input_id = snapshot["source_input_id"]
            expected_keys = {prefix + "file:" + command.key for command in commands}
            if set(records) - expected_keys:
                raise RuntimeError("hook batch contains an unexpected execution identity")
            # Validate the whole recovered batch before starting any new effect.
            for command in commands:
                current = current_commands.get(command.key)
                if current is not None and current != command:
                    raise RuntimeError("hook execution identity collision during batch recovery")
                record = records.get(prefix + "file:" + command.key)
                if record is not None:
                    if record["request"] != _execution_request(command, payload):
                        raise RuntimeError(
                            "hook execution identity collision during batch recovery"
                        )
                    if record["result"] is None and not (
                        command.asynchronous
                        and self._async.owns(prefix + "file:" + command.key, record["request"])
                    ):
                        raise RuntimeError(
                            "Previous hook outcome is unknown; execution was not repeated."
                        )
                elif current is None:
                    raise RuntimeError(
                        "Pending hook authorization was removed; batch was not resumed."
                    )
        feedback = []
        stopped = False
        # Admit all asynchronous work before running synchronous commands. A
        # slow earlier sync command must not delay a later async declaration.
        for command in commands:
            if command.asynchronous:
                await self._start_async(
                    command,
                    payload,
                    prefix + "file:" + command.key,
                    state=state,
                    repository=repository,
                    shell=shell,
                    settings=settings,
                )
        commands = tuple(command for command in commands if not command.asynchronous)
        for command in commands:
            key = prefix + "file:" + command.key
            await runtime.events.emit(
                HookStarted(
                    state["thread_id"],
                    state["turn_id"],
                    HookRunSummary(key, command.key, event_name, "running", command.status_message),
                )
            )

        async def execute(command):
            # Config identity is canonical; execution identity must retain its
            # original spelling across upgrades. Renaming this would bypass
            # unknown-outcome protection and duplicate durable feedback IDs.
            key = prefix + "file:" + command.key
            request = _execution_request(command, payload)
            result = await repository.claim_hook_execution(
                state["thread_id"], state["turn_id"], key, request
            )
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
                            environment={**self.environment, **dict(command.environment)},
                        )
                except (OSError, ValueError, TimeoutError) as error:
                    result = {"error": f"Stop hook failed: {type(error).__name__}: {error}"}
                await repository.complete_hook_execution(
                    state["thread_id"], state["turn_id"], key, request, result
                )
            return result

        # Native local synchronous handlers execute concurrently, then aggregate
        # in configuration order. Keep each effect's claim/result independent.
        tasks = [asyncio.create_task(execute(command)) for command in commands]
        try:
            results = await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done() and not task.cancelling():
                    task.cancel()
            # A journal failure must not leave sibling effects running after the
            # Turn/repository closes. Repeated cancellation cannot abandon join.
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

        for command, result in zip(commands, results, strict=True):
            key = prefix + "file:" + command.key
            decision, text, diagnostics = outcome(result)
            # An allow result carries text only for execution/parse failure;
            # successful diagnostics live separately. Never classify by wording.
            status = {"block": "blocked", "stop": "stopped"}.get(
                decision, "failed" if text else "completed"
            )
            entries = tuple(HookOutputEntry("warning", message) for message in diagnostics)
            if text:
                kind = {"block": "feedback", "stop": "stop"}.get(decision, "error")
                entries += (HookOutputEntry(kind, text),)
            await runtime.events.emit(
                HookCompleted(
                    state["thread_id"],
                    state["turn_id"],
                    HookRunSummary(
                        key, command.key, event_name, status, command.status_message, entries
                    ),
                )
            )
            if decision == "block":
                item = ContextItem(
                    key=key,
                    role=ContextRole.USER,
                    content=text,
                    turn_id=state["turn_id"],
                    id=ItemId(str(uuid5(NAMESPACE_URL, key))),
                    content_kind=f"hook.{event_key}.feedback",
                    source_input_id=source_input_id,
                )
                feedback.append(item)
            else:
                stopped |= decision == "stop"
        # Aggregate before publishing any continuation. A stop from any handler
        # dominates block, regardless of declaration order. Execution outcomes
        # remain journaled individually for cold recovery and unknown-result safety.
        if stopped:
            return StopDecision.STOP
        for item in feedback:
            if not any(i.id == item.id for i in history):
                await repository.append_items(state["thread_id"], (item,))
        return StopDecision.BLOCK if feedback else StopDecision.ALLOW
