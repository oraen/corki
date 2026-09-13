"""Synchronous Post output schema and control precedence from Codex hooks."""

import json

import pytest

from corki.core.post_tool_hooks import outcome


@pytest.mark.parametrize(
    "extra",
    [
        {"suppressOutput": 0},
        {"suppressOutput": None},
        {"suppressOutput": []},
        {"unknown": True},
        {"hookSpecificOutput": {}},
        {"hookSpecificOutput": {"hookEventName": "PostToolUse", "unknown": True}},
    ],
)
def test_invalid_wire_does_not_apply_controls(extra):
    value = {"decision": "block", "reason": "do not accept", "systemMessage": "warning", **extra}
    blocked, feedback, context, warning, error = outcome(
        {"exit_code": 0, "stdout": json.dumps(value)}
    )
    assert (blocked, feedback, context, warning) == (False, None, None, None)
    assert error


@pytest.mark.parametrize(
    "control",
    [
        {"suppressOutput": True},
        {"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedMCPToolOutput": {}}},
        {"decision": "block", "reason": " "},
    ],
)
def test_stop_precedes_unsupported_controls_but_drops_context(control):
    value = {
        "continue": False,
        "stopReason": "STOP",
        "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "DROP"},
        **control,
    }
    assert outcome({"exit_code": 0, "stdout": json.dumps(value)}) == (
        False,
        "STOP",
        None,
        None,
        None,
    )


def test_explicit_empty_stop_reason_is_not_replaced_by_default():
    assert outcome({"exit_code": 0, "stdout": '{"continue":false,"stopReason":""}'}) == (
        False,
        "",
        None,
        None,
        None,
    )


@pytest.mark.parametrize("event", ["PostToolUse", "PreToolUse", "Stop"])
def test_native_wire_enum_is_not_replaced_with_schema_only_event_restriction(event):
    # The Rust wire field is HookEventNameWire; the Post-only schemars annotation
    # does not impose a second runtime check in parse_post_tool_use.
    value = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": "context"}}
    assert outcome({"exit_code": 0, "stdout": json.dumps(value)}) == (
        False,
        None,
        "context",
        None,
        None,
    )
