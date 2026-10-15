import json

import pytest

from corki.core.prompt_hook_output import PromptHookOutput, outcome
from corki.protocol.events import HookOutputEntry


def result(value):
    return {"exit_code": 0, "stdout": json.dumps(value), "stderr": ""}


@pytest.mark.parametrize("control", [True, False])
@pytest.mark.parametrize("raw", [" plain context ", "123", "null", '"text"'])
def test_plain_stdout_is_context(raw, control):
    assert outcome({"exit_code": 0, "stdout": raw}, control=control) == PromptHookOutput(
        context=raw.strip()
    )


@pytest.mark.parametrize("control", [True, False])
@pytest.mark.parametrize(
    "value",
    [
        [],
        {"continue": None},
        {"continue": 0},
        {"suppressOutput": "false"},
        {"decision": "approve"},
        {"reason": []},
        {"unknown": True},
        {"hookSpecificOutput": {}},
        {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": []}},
    ],
)
def test_invalid_wire_never_controls_or_injects(value, control):
    parsed = outcome(result(value), control=control)
    assert not parsed.stopped and parsed.context is None and parsed.status == "failed"
    assert len(parsed.entries) == 1 and parsed.entries[0].kind == "error"


@pytest.mark.parametrize("control", [True, False])
@pytest.mark.parametrize("stopping", [True, False])
@pytest.mark.parametrize("reason", [None, " ", "reject"])
def test_block_stop_precedence_and_async_context(control, stopping, reason):
    parsed = outcome(
        result(
            {
                "continue": not stopping,
                "stopReason": "stop",
                "decision": "block",
                "reason": reason,
                "systemMessage": "warning",
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": "context",
                },
            }
        ),
        control=control,
    )
    invalid = reason is None or not reason.strip()
    assert parsed.stopped == (control and (stopping or not invalid))
    assert parsed.context == (None if control and invalid else "context")
    assert parsed.status == (
        "completed"
        if not control
        else "stopped"
        if stopping
        else "failed"
        if invalid
        else "blocked"
    )
    assert parsed.entries[0] == HookOutputEntry("warning", "warning")


@pytest.mark.parametrize("control", [True, False])
@pytest.mark.parametrize("stderr", ["", "  ", " reject "])
def test_exit_two_requires_reason_and_synchronous_control(control, stderr):
    parsed = outcome({"exit_code": 2, "stderr": stderr, "stdout": "do not inject"}, control=control)
    assert parsed.stopped == bool(control and stderr.strip())
    assert parsed.context is None
    assert parsed.status == ("blocked" if parsed.stopped else "failed")


def test_reason_without_decision_is_not_invalid_and_suppress_output_is_ignored():
    assert outcome(result({"reason": "unused", "suppressOutput": True})) == PromptHookOutput()
