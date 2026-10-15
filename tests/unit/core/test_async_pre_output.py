"""Async Pre output validates wire but never grants synchronous control."""

import json

import pytest

from corki.core.async_pre_output import outcome


@pytest.mark.parametrize(
    "control",
    [
        {"continue": False, "stopReason": "stop"},
        {"decision": "block", "reason": "deny"},
        {"suppressOutput": True},
        {"decision": "approve"},
    ],
)
@pytest.mark.parametrize("permission", ["allow", "deny", "ask"])
def test_async_control_does_not_discard_context_or_rewrite(control, permission):
    wire = {
        **control,
        "systemMessage": "warning",
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": permission,
            "updatedInput": ["arbitrary JSON, not a synchronous rewrite"],
            "additionalContext": "context",
        },
    }
    assert outcome({"exit_code": 0, "stdout": json.dumps(wire)}) == (
        False,
        None,
        "context",
        "warning",
        None,
    )


@pytest.mark.parametrize(
    "wire",
    [
        [],
        {"unknown": True},
        {"continue": None},
        {"suppressOutput": 1},
        {"decision": "deny"},
        {"reason": 3},
        {"systemMessage": []},
        {"hookSpecificOutput": {}},
        {"hookSpecificOutput": {"hookEventName": "unknown"}},
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "unknown": 1}},
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "block"}},
        {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": 1}},
    ],
)
def test_invalid_wire_has_no_feedback(wire):
    result = outcome({"exit_code": 0, "stdout": json.dumps(wire)})
    assert result[:4] == (False, None, None, None)
    assert result[4].startswith("Invalid PreToolUse output")


def test_async_exit_two_is_error_not_block():
    result = outcome({"exit_code": 2, "stderr": "deny", "stdout": "{}"})
    assert result[:4] == (False, None, None, None)
    assert result[4] == "PreToolUse exited with status 2"
