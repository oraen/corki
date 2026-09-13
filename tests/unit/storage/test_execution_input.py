"""Executed input is host ledger metadata, with explicit legacy absence."""

import json

import pytest

from corki.protocol.ids import new_tool_call_id
from corki.protocol.tools import ToolResult
from corki.storage.sqlite import _result_from_json, _result_to_json


def test_legacy_result_roundtrip_does_not_invent_executed_input():
    result = ToolResult(new_tool_call_id(), "probe", "done")
    encoded = _result_to_json(result)
    assert "execution_input_json" not in json.loads(encoded)
    assert "post_tool_use_json" not in json.loads(encoded)
    assert "code_mode_lifecycle_json" not in json.loads(encoded)
    assert _result_from_json(encoded).execution_input_json is None
    assert _result_from_json(encoded).post_tool_use_json is None
    assert _result_from_json(encoded).code_mode_lifecycle_json is None
    assert _result_to_json(_result_from_json(encoded)) == encoded


@pytest.mark.parametrize("status", ["running", "completed", "failed", "terminated"])
def test_cell_lifecycle_roundtrip(status):
    snapshot = json.dumps(
        {"version": 1, "cell_id": "cell", "parent_call_id": "parent", "status": status}
    )
    result = ToolResult(new_tool_call_id(), "exec", "output", code_mode_lifecycle_json=snapshot)
    assert _result_from_json(_result_to_json(result)).code_mode_lifecycle_json == snapshot


@pytest.mark.parametrize(
    "key,value",
    [
        ("version", True),
        ("version", 2),
        ("cell_id", ""),
        ("parent_call_id", None),
        ("status", []),
        ("status", "unknown"),
        ("extra", 1),
    ],
)
def test_invalid_cell_lifecycle_rejected(key, value):
    snapshot = {"version": 1, "cell_id": "cell", "parent_call_id": "parent", "status": "running"}
    snapshot[key] = value
    with pytest.raises(ValueError, match="Code Mode lifecycle"):
        ToolResult(
            new_tool_call_id(), "exec", "output", code_mode_lifecycle_json=json.dumps(snapshot)
        )


@pytest.mark.parametrize("kind", ["json", "freeform"])
def test_execution_input_roundtrips_as_immutable_host_json(kind):
    snapshot = json.dumps(
        {
            "version": 1,
            "input_kind": kind,
            "arguments": {"n": 1} if kind == "json" else None,
            "raw_arguments": '{"n":1}' if kind == "json" else "text(1)",
        }
    )
    result = ToolResult(new_tool_call_id(), "probe", "done", execution_input_json=snapshot)
    assert _result_from_json(_result_to_json(result)).execution_input_json == snapshot


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("version", 2),
        ("input_kind", "namespace"),
        ("input_kind", []),
        ("arguments", []),
        ("raw_arguments", None),
        ("extra", "unknown"),
    ],
)
def test_corrupt_execution_snapshot_is_not_accepted(field, value):
    snapshot = {"version": 1, "input_kind": "json", "arguments": {}, "raw_arguments": "{}"}
    snapshot[field] = value
    with pytest.raises(ValueError):
        ToolResult(new_tool_call_id(), "probe", "done", execution_input_json=json.dumps(snapshot))


@pytest.mark.parametrize("exit_code", [0, 1])
@pytest.mark.parametrize("running", [False, True])
def test_shell_post_payload_eligibility_and_cold_roundtrip(tmp_path, exit_code, running):
    from corki.protocol.terminals import BackgroundTerminalInfo
    from corki.protocol.tools import ToolCall
    from corki.tools.base import ToolContext
    from corki.tools.builtin.process import ProcessObservation
    from corki.tools.builtin.shell_output import shell_result

    call = ToolCall(new_tool_call_id(), "write_stdin", {"session_id": "session"})
    observation = ProcessObservation(
        "output",
        None if running else exit_code,
        "session" if running else None,
        terminal_info=BackgroundTerminalInfo("original-call", "session", "printf output", tmp_path),
    )
    result = shell_result(call, observation, ToolContext(tmp_path))
    recovered = _result_from_json(_result_to_json(result))
    assert not result.is_error
    if running:
        assert recovered.post_tool_use_json is None
    else:
        payload = json.loads(recovered.post_tool_use_json)
        assert payload["tool_use_id"] == "original-call"
        assert payload["tool_response"] == "output"
