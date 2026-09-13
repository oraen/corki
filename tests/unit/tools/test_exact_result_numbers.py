"""Exact JSON overrides are bounded and durable, not opaque serialized objects."""

import pytest

from corki.protocol.ids import new_tool_call_id
from corki.protocol.tools import CodeModeOutput, ToolResult
from corki.protocol.wire_numbers import WireNumber, dumps_wire
from corki.storage.sqlite import _result_from_json, _result_to_json


@pytest.mark.parametrize("token", ["1e999", "1e-999", "1.234567890123456789", "1.00", "9" * 700])
def test_exact_json_override_survives_normalization_and_ledger_roundtrip(token):
    value = {"n": WireNumber(token)}
    result = ToolResult(
        new_tool_call_id(), "read", "ok", code_mode_output=CodeModeOutput(value).normalized()
    )
    encoded = _result_to_json(result)
    restored = _result_from_json(encoded)
    assert dumps_wire(restored.code_mode_output.value) == dumps_wire(value)
    assert _result_to_json(restored) == encoded


def test_exact_number_still_obeys_structured_result_byte_limit(monkeypatch):
    monkeypatch.setattr("corki.protocol.tools.MAX_CODE_MODE_RESULT_BYTES", 32)
    with pytest.raises(ValueError, match="byte limit"):
        CodeModeOutput({"n": WireNumber("9" * 40)}).normalized()
