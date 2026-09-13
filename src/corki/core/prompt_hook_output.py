"""UserPromptSubmit output semantics, independent of durable input admission."""

import json
from dataclasses import dataclass

from corki.protocol.events import HookOutputEntry


@dataclass(frozen=True)
class PromptHookOutput:
    stopped: bool = False
    status: str = "completed"
    context: str | None = None
    entries: tuple[HookOutputEntry, ...] = ()


def outcome(result, *, control=True):
    """Parse trusted execution output; async callers disable control effects."""
    error = result.get("error")
    code = result.get("exit_code")
    if error:
        return PromptHookOutput(status="failed", entries=(HookOutputEntry("error", str(error)),))
    if code == 2 and control:
        reason = result.get("stderr", "").strip()
        if reason:
            return PromptHookOutput(True, "blocked", entries=(HookOutputEntry("feedback", reason),))
        error = "UserPromptSubmit exit 2 requires a non-empty stderr reason"
    elif code != 0:
        error = f"UserPromptSubmit exited with status {code}"
    if error:
        return PromptHookOutput(status="failed", entries=(HookOutputEntry("error", error),))
    raw = result.get("stdout", "").strip()
    if not raw:
        return PromptHookOutput()
    if not raw.startswith(("{", "[")):
        return PromptHookOutput(context=raw)
    try:
        value = json.loads(raw)
        json.dumps(value, allow_nan=False, ensure_ascii=False).encode("utf-8")
        if not isinstance(value, dict) or set(value) - {
            "continue",
            "stopReason",
            "suppressOutput",
            "systemMessage",
            "decision",
            "reason",
            "hookSpecificOutput",
        }:
            raise ValueError("Invalid UserPromptSubmit output object")
        for key in ("continue", "suppressOutput"):
            if key in value and type(value[key]) is not bool:
                raise ValueError(f"{key} must be boolean")
        for key in ("stopReason", "systemMessage", "reason"):
            if value.get(key) is not None and not isinstance(value[key], str):
                raise ValueError(f"{key} must be a string")
        if value.get("decision") not in (None, "block"):
            raise ValueError("Invalid UserPromptSubmit decision")
        specific = value.get("hookSpecificOutput")
        if specific is not None:
            if not isinstance(specific, dict) or set(specific) - {
                "hookEventName",
                "additionalContext",
            }:
                raise ValueError("Invalid UserPromptSubmit specific output")
            # The reference wire uses the shared enum; its event-specific schema
            # annotation does not narrow serde's accepted enum variants.
            if specific.get("hookEventName") not in (
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
            ):
                raise ValueError("Invalid or missing Hook event")
            context = specific.get("additionalContext")
            if context is not None and not isinstance(context, str):
                raise ValueError("additionalContext must be a string")
        else:
            context = None
    except (ValueError, TypeError, RecursionError) as error:
        return PromptHookOutput(status="failed", entries=(HookOutputEntry("error", str(error)),))

    entries = (
        (HookOutputEntry("warning", value["systemMessage"]),)
        if value.get("systemMessage") is not None
        else ()
    )
    reason = value.get("reason")
    invalid_block = value.get("decision") == "block" and (not reason or not reason.strip())
    if not control:
        return PromptHookOutput(context=context, entries=entries)
    if invalid_block:
        context = None
    if value.get("continue") is False:
        if value.get("stopReason") is not None:
            entries += (HookOutputEntry("stop", value["stopReason"]),)
        return PromptHookOutput(True, "stopped", context, entries)
    if invalid_block:
        entries += (HookOutputEntry("error", "UserPromptSubmit block requires a non-empty reason"),)
        return PromptHookOutput(status="failed", entries=entries)
    if value.get("decision") == "block":
        return PromptHookOutput(
            True, "blocked", context, entries + (HookOutputEntry("feedback", reason),)
        )
    return PromptHookOutput(context=context, entries=entries)
