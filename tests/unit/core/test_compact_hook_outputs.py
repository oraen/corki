import pytest

from corki.core.compact_hooks import outcome


@pytest.mark.parametrize("text", ["", "plain", "1", "null", '"text"', "true"])
def test_plain_output_is_ignored(text):
    assert outcome({"exit_code": 0, "stdout": text}) == (False, "completed", ())


@pytest.mark.parametrize(
    "text",
    [
        "{",
        "[]",
        '{"continue":null}',
        '{"continue":0}',
        '{"suppressOutput":"false"}',
        '{"stopReason":false}',
        '{"decision":"block","reason":"no"}',
        '{"hookSpecificOutput":{}}',
    ],
)
def test_invalid_wire_is_failure_not_control(text):
    stopped, status, entries = outcome({"exit_code": 0, "stdout": text})
    assert not stopped and status == "failed" and entries[0].kind == "error"


@pytest.mark.parametrize("control", [True, False])
def test_async_output_cannot_stop(control):
    stopped, status, entries = outcome(
        {
            "exit_code": 0,
            "stdout": '{"continue":false,"stopReason":"pause","systemMessage":"notice"}',
        },
        control=control,
    )
    assert stopped == control
    assert status == ("stopped" if control else "completed")
    assert entries[0].text == "notice"
    assert len(entries) == (2 if control else 1)
