"""Hook metadata variants affect plugin admission even without hook execution."""

import json

import pytest

from corki.plugins.agent_overlay import parse_overlay

RAW = "$serde_json::private::RawValue"
BAD = {RAW: "not-json"}


def inline(handler):
    return {"hooks": {"Stop": [{"hooks": [handler]}]}}


CASES = [
    ("bad-private-root", BAD, False),
    ("bad-private-list", [BAD], False),
    ("unknown-root", {"future": BAD}, False),
    ("unknown-event", {"hooks": {"future": BAD}}, True),
    ("unknown-group", {"hooks": {"Stop": [{"future": BAD}]}}, True),
    ("invalid-ordinary-shape", {"hooks": None}, True),
    ("paths", ["./one.json", "./two.json"], True),
    ("inline-list", [{"hooks": {"future": BAD}}, {}], True),
    ("struct-sequence", [None, {"future": BAD}], True),
    ("group-sequence", {"hooks": {"Stop": [[None, [{"type": "prompt", "future": BAD}]]]}}, True),
    ("description-type", {"description": 1, "hooks": {"future": BAD}}, False),
    ("event-type", {"hooks": {"Stop": None, "future": BAD}}, False),
    ("matcher-type", {"hooks": {"Stop": [{"matcher": 1, "future": BAD}]}}, False),
]
for kind in ("command", "mcp_tool", "prompt", "agent"):
    handler = {"type": kind, "future": BAD}
    if kind == "command":
        handler["command"] = "must-not-execute"
    if kind == "mcp_tool":
        handler.update(server="fixture", tool="write")
    CASES.append((kind + "-ignored-field", inline(handler), True))

for field, value in (
    ("command", False),
    ("commandWindows", False),
    ("command_windows", False),
    ("timeout", -1),
    ("timeout", 1.0),
    ("timeout", 2**64),
    ("async", None),
    ("async", 1),
    ("statusMessage", []),
    ("additionalContextLimit", -1),
):
    CASES.append(
        (
            f"command-{field}-{value}",
            inline({"type": "command", "command": "x", "future": BAD, field: value}),
            False,
        )
    )
for field, value in (("timeout", 2**64 - 1), ("timeout", None), ("command_windows", "x")):
    CASES.append(
        (
            f"command-valid-{field}-{value}",
            inline({"type": "command", "command": "x", "future": BAD, field: value}),
            True,
        )
    )
for name, values, valid in (
    ("null", {"x": None}, False),
    ("nested-null", {"x": [None]}, False),
    ("huge-number", {"x": 2**129}, True),
    ("raw-input", {"x": {RAW: '{"ok":true}'}}, True),
    ("bad-raw-input", {"x": BAD}, False),
    ("map-key-not-value", {RAW: "not-json"}, True),
):
    CASES.append(
        (
            "mcp-" + name,
            inline(
                {
                    "type": "mcp_tool",
                    "server": "fixture",
                    "tool": "write",
                    "input": values,
                    "future": BAD,
                }
            ),
            valid,
        )
    )


@pytest.mark.parametrize(("name", "hooks", "valid"), CASES, ids=[case[0] for case in CASES])
def test_hook_untagged_admission(name, hooks, valid):
    contents = json.dumps({"hooks": hooks})
    if valid:
        assert "hooks" in parse_overlay(contents)
    else:
        with pytest.raises(ValueError):
            parse_overlay(contents)


@pytest.mark.parametrize(
    "body",
    [
        '{"description":null,"description":null,"hooks":{"future":BAD}}',
        '{"hooks":{"Stop":[],"Stop":[],"future":BAD}}',
        '{"hooks":{"Stop":[{"matcher":null,"matcher":null,"future":BAD}]}}',
        '{"hooks":{"Stop":[{"hooks":[{"type":"command","type":"command","command":"x","future":BAD}]}]}}',
        '{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"x","commandWindows":null,"command_windows":null,"future":BAD}]}]}}',
    ],
)
def test_duplicate_typed_hook_fields_cannot_bypass_invalid_value_fallback(body):
    with pytest.raises(ValueError):
        parse_overlay('{"hooks":' + body.replace("BAD", json.dumps(BAD)) + "}")


def test_invalid_mcp_list_fallback_still_deserializes_value():
    with pytest.raises(ValueError):
        parse_overlay(json.dumps({"mcpServers": [BAD]}))
