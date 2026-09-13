import pytest

from corki.core.mcp_tool_hooks import expand
from corki.core.stop_hooks import command_identity


def test_mcp_template_preserves_types_and_does_not_reexpand_event_data():
    event = {"tool_input": {"count": 3, "enabled": True, "value": "${missing}", "none": None}}
    template = {
        "count": "${tool_input.count}",
        "text": "count=${tool_input.count};enabled=${tool_input.enabled}",
        "nested": ["${tool_input.none}", "${tool_input}", "${tool_input.value}"],
    }
    result = expand(template, event)
    assert result == {
        "count": 3,
        "text": "count=3;enabled=true",
        "nested": [None, event["tool_input"], "${missing}"],
    }
    result["nested"][1]["count"] = 7
    assert event["tool_input"]["count"] == 3


@pytest.mark.parametrize("template", ["${missing}", "prefix-${tool_input.absent}", "${x.0}"])
def test_missing_template_field_is_not_sent_unresolved(template):
    with pytest.raises(ValueError, match="was not found"):
        expand({"value": template}, {"tool_input": {}, "x": [1]})


def test_mcp_trust_identity_binds_target_template_timeout_and_matcher():
    handler = {"type": "mcp_tool", "server": "policy", "tool": "review", "input": {"x": 1}}
    original, normalized = command_identity(handler, event_name="PreToolUse", matcher="probe")
    assert normalized["timeout"] == 600
    for change in ({"server": "other"}, {"tool": "other"}, {"input": {"x": 2}}, {"timeout": 3}):
        changed, _ = command_identity(
            {**handler, **change}, event_name="PreToolUse", matcher="probe"
        )
        assert changed != original
    assert command_identity(handler, event_name="PreToolUse", matcher="other")[0] != original
    assert (
        command_identity({**handler, "timeout": 600}, event_name="PreToolUse", matcher="probe")[0]
        == original
    )


@pytest.mark.parametrize(
    "change",
    [{"server": " "}, {"tool": None}, {"input": []}, {"input": {"x": None}}, {"timeout": True}],
)
def test_invalid_mcp_hook_configuration_is_rejected(change):
    with pytest.raises(ValueError):
        command_identity({"type": "mcp_tool", "server": "s", "tool": "t", **change})
