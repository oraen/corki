"""Interrupt has its own bounded discovery and data-only output contract."""

import pytest

from corki.core.interrupt_hooks import outcome, validate_plan_identity
from corki.core.stop_hooks import command_identity


@pytest.mark.parametrize(
    "raw,status,warning",
    [
        ("", "completed", None),
        ("  ", "completed", None),
        ("{}", "completed", None),
        ('{"systemMessage":null}', "completed", None),
        ('{"systemMessage":"notice"}', "completed", "notice"),
        ('{"systemMessage":""}', "completed", ""),
        ('{"systemMessage":false}', "failed", None),
        ('{"continue":false}', "failed", None),
        ('{"decision":"block"}', "failed", None),
        ('{"hookSpecificOutput":{"additionalContext":"x"}}', "failed", None),
        ("plain text", "failed", None),
        ("{", "failed", None),
        ("[]", "failed", None),
        ("null", "failed", None),
    ],
)
def test_interrupt_output_never_controls_or_injects_context(raw, status, warning):
    parsed = outcome({"exit_code": 0, "stdout": raw, "stderr": "ignored"})
    assert parsed.status == status
    assert not parsed.stopped and parsed.context is None
    assert [entry.text for entry in parsed.entries if entry.kind == "warning"] == (
        [] if warning is None else [warning]
    )
    assert bool([entry for entry in parsed.entries if entry.kind == "error"]) == (
        status == "failed"
    )


@pytest.mark.parametrize("code", [None, 1, 2, 127])
def test_interrupt_nonzero_is_diagnostic_not_stop(code):
    parsed = outcome({"exit_code": code, "stdout": "{}", "stderr": "block this turn"})
    assert parsed.status == "failed"
    assert not parsed.stopped and parsed.context is None
    assert all(entry.kind == "error" for entry in parsed.entries)


@pytest.mark.parametrize("kind", ["command", "mcp_tool"])
@pytest.mark.parametrize("timeout,expected", [(None, 1), (0, 1), (2, 2), (600, 3)])
def test_interrupt_identity_ignores_matcher_and_caps_all_handler_timeouts(kind, timeout, expected):
    handler = (
        {"type": "command", "command": "echo hook", "async": True}
        if kind == "command"
        else {"type": "mcp_tool", "server": "local", "tool": "hook"}
    )
    if timeout is not None:
        handler["timeout"] = timeout
    fingerprint, normalized = command_identity(handler, event_name="Interrupt")
    assert normalized["timeout"] == expected
    assert command_identity(handler, event_name="Interrupt", matcher="ignored") == (
        fingerprint,
        normalized,
    )
    if kind == "command":
        assert normalized["async"] is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("version", 2),
        ("commands", []),
        ("commands", {}),
        ("payload", None),
        ("session_id", "foreign"),
        ("turn_id", "foreign"),
        ("hook_event_name", "Stop"),
    ],
)
def test_legacy_interrupt_evidence_requires_matching_plan_identity(field, value):
    plan = {
        "version": 1,
        "commands": [{"key": "command"}],
        "payload": {"session_id": "session", "turn_id": "turn", "hook_event_name": "Interrupt"},
    }
    validate_plan_identity(plan, "session", "turn")
    if field in plan:
        plan[field] = value
    else:
        plan["payload"][field] = value
    with pytest.raises(ValueError, match="Interrupt plan identity mismatch"):
        validate_plan_identity(plan, "session", "turn")
