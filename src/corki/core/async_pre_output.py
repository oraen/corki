"""PreToolUse wire validation without synchronous permission/control effects."""

import json


def outcome(result):
    if result.get("error"):
        return False, None, None, None, str(result["error"])
    if result.get("exit_code") != 0:
        return False, None, None, None, f"PreToolUse exited with status {result.get('exit_code')}"
    raw = result.get("stdout", "").strip()
    if not raw.startswith(("{", "[")):
        return False, None, None, None, None
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
            raise ValueError("Invalid PreToolUse output object")
        for key in ("continue", "suppressOutput"):
            if key in value and type(value[key]) is not bool:
                raise ValueError(f"{key} must be boolean")
        for key in ("stopReason", "systemMessage", "reason"):
            if value.get(key) is not None and not isinstance(value[key], str):
                raise ValueError(f"{key} must be a string")
        if value.get("decision") not in (None, "approve", "block"):
            raise ValueError("Invalid PreToolUse decision")
        specific = value.get("hookSpecificOutput")
        if specific is not None:
            if not isinstance(specific, dict) or set(specific) - {
                "hookEventName",
                "permissionDecision",
                "permissionDecisionReason",
                "updatedInput",
                "additionalContext",
            }:
                raise ValueError("Invalid PreToolUse specific output")
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
            if specific.get("permissionDecision") not in (None, "allow", "deny", "ask"):
                raise ValueError("Invalid PreToolUse permission decision")
            for key in ("permissionDecisionReason", "additionalContext"):
                if specific.get(key) is not None and not isinstance(specific[key], str):
                    raise ValueError(f"{key} must be a string")
        return (
            False,
            None,
            (specific or {}).get("additionalContext"),
            value.get("systemMessage"),
            None,
        )
    except (ValueError, TypeError, RecursionError) as error:
        return False, None, None, None, f"Invalid PreToolUse output: {error}"
