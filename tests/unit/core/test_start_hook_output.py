"""Start control differs from prompt rejection and context-only subagent starts."""

import json

import pytest

from corki.core.start_hooks import outcome


@pytest.mark.parametrize("event", ["SessionStart", "SubagentStart"])
@pytest.mark.parametrize("control", [False, True])
@pytest.mark.parametrize(
    "policy", ["stop", "plain", "exit2", "decision", "bad_boolean", "bad_json"]
)
def test_start_output(event, control, policy):
    stdout = json.dumps(
        {
            "continue": False,
            "hookSpecificOutput": {
                "hookEventName": event,
                "additionalContext": "CONTEXT",
            },
        }
    )
    if policy == "plain":
        stdout = " CONTEXT "
    elif policy == "decision":
        stdout = '{"decision":"block","reason":"reject"}'
    elif policy == "bad_boolean":
        stdout = '{"continue":0}'
    elif policy == "bad_json":
        stdout = '{"continue":'
    parsed = outcome(
        {"exit_code": 2 if policy == "exit2" else 0, "stdout": stdout, "stderr": "reject"},
        event=event,
        control=control,
    )
    stopped = policy == "stop" and control and event == "SessionStart"
    assert parsed.stopped == stopped
    assert parsed.context == ("CONTEXT" if policy in {"stop", "plain"} else None)
    assert parsed.status == (
        "stopped" if stopped else "completed" if policy in {"stop", "plain"} else "failed"
    )
