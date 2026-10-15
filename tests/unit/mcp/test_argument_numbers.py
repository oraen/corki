"""Native Value parsing preserves MCP business numbers before remote dispatch."""

import pytest

from corki.mcp.arguments import call_arguments
from corki.mcp.json_rpc import MCPProtocolError
from corki.protocol.ids import new_tool_call_id
from corki.protocol.tools import ToolCall
from corki.protocol.wire_numbers import dumps_wire


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1.234567890123456789", "1.234567890123456789"),
        ("1e999", "1e+999"),
        ("1e-999", "1e-999"),
        ("0E00999", "0e+00999"),
        ("1.2300E+0003", "1.2300e+0003"),
        ("9" * 5000, "9" * 5000),
        (str(2**100), str(2**100)),
        ("-0", "0"),
        ("-0.0", "-0.0"),
        ('{"$serde_json::private::Number":"1e999"}', "1e+999"),
        ('{"$serde_json::private::RawValue":"[1e999]"}', "[1e+999]"),
        ('{"x":1,"x":2}', '{"x":2}'),
        ('"1e999"', '"1e999"'),
    ],
)
def test_raw_argument_value_spelling(raw, expected):
    text = '{"n":' + raw + "}"
    call = ToolCall(new_tool_call_id(), "mcp__docs::read", {}, raw_arguments=text)
    assert dumps_wire(call_arguments(call)) == '{"n":' + expected + "}"
    assert call.raw_arguments == text and call.arguments == {}


@pytest.mark.parametrize(
    "raw",
    [
        '{"n":NaN}',
        '{"n":Infinity}',
        '{"n":01}',
        '{"n":1e}',
        '{"n":"\\ud800"}',
        '{"\\ud800":0}',
        '{"n":{"$serde_json::private::Number":"NaN"}}',
        '{"n":{"$serde_json::private::RawValue":"["}}',
        '{"n":' + "[" * 127 + "0" + "]" * 127 + "}",
    ],
)
def test_invalid_raw_value_is_protocol_error(raw):
    call = ToolCall(new_tool_call_id(), "mcp__docs::read", {}, raw_arguments=raw)
    with pytest.raises(MCPProtocolError, match="arguments"):
        call_arguments(call)


@pytest.mark.parametrize("provider", ["responses", "compatible"])
def test_provider_cache_cannot_poison_commit_before_mcp_parses_raw(provider):
    from types import SimpleNamespace

    from corki.models.openai_compatible import _finish_tool_call
    from corki.models.responses import _finish_function_call

    raw = '{"n":1e999}'
    buffer = SimpleNamespace(arguments=raw, id="numeric", call_id="numeric", name="mcp__docs::read")
    call = (_finish_function_call if provider == "responses" else _finish_tool_call)(buffer)
    assert call.arguments is None and call.parse_error
    assert call.raw_arguments == raw
    assert dumps_wire(call_arguments(call)) == '{"n":1e+999}'
