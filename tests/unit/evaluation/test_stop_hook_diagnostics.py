"""Universal messages survive control decisions and semantic block failures."""

import json

import pytest

from corki.core.stop_hooks import outcome


@pytest.mark.parametrize("suppress", [False, True])
@pytest.mark.parametrize(
    "control",
    [{}, {"continue": False}, {"decision": "block", "reason": "check"}, {"decision": "block"}],
)
def test_system_message_is_independent_of_control(control, suppress):
    result = outcome(
        {
            "exit_code": 0,
            "stderr": "",
            "stdout": json.dumps(
                {**control, "systemMessage": "notice", "suppressOutput": suppress}
            ),
        }
    )
    assert result[2] == ("notice",)
    expected = (
        "stop" if control.get("continue") is False else "block" if "reason" in control else "allow"
    )
    assert result[0] == expected
    if control == {"decision": "block"}:
        assert "invalid" in result[1]


def test_structurally_invalid_output_does_not_emit_unvalidated_message():
    decision, message, diagnostics = outcome(
        {
            "exit_code": 0,
            "stderr": "",
            "stdout": '{"systemMessage":"notice","continue":"false"}',
        }
    )
    assert decision == "allow"
    assert "invalid" in message
    assert diagnostics == ()
