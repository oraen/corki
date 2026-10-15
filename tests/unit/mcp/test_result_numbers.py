"""Remote JSON numbers survive public result conversion without float coercion."""

import asyncio

import pytest

from corki.mcp.client import _read_stdio, validate_tool_result
from corki.mcp.inbound import InboundService
from corki.mcp.json_rpc import MCPProtocolError, decode_json
from corki.mcp.output import mcp_output
from corki.protocol.ids import new_tool_call_id
from corki.protocol.tools import ToolCall
from corki.protocol.truncation import TruncationPolicy
from corki.protocol.wire_numbers import WireNumber, dumps_wire
from corki.tools import ToolContext


@pytest.mark.parametrize(
    "token",
    [
        "1e999",
        "1e-999",
        "1.234567890123456789",
        "1.00",
        "9" * 700,
        "18446744073709551616",
        "-9223372036854775809",
        "0.5",
    ],
)
def test_exact_json_number_reaches_model_event_and_public_result(tmp_path, token):
    raw = decode_json('{"content":[],"structuredContent":{"n":' + token + "}}")
    expected = '{"n":' + WireNumber(token).token + "}"
    result = mcp_output(
        ToolCall(new_tool_call_id(), "read", {}),
        validate_tool_result(raw),
        context=ToolContext(tmp_path),
        policy=TruncationPolicy(),
        wall_time=0,
    )
    assert result.content.endswith(expected)
    assert expected in result.mcp_result_json
    assert dumps_wire(result.code_mode_output.value["structuredContent"]) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ('{"$serde_json::private::Number":"1e999"}', "1e+999"),
        ('{"$serde_json::private::RawValue":"[1e999]"}', "[1e+999]"),
        (
            '{"normal":1,"$serde_json::private::Number":"not a number"}',
            '{"normal":1,"$serde_json::private::Number":"not a number"}',
        ),
    ],
)
def test_json_value_marker_is_interpreted_only_at_value_boundary(value, expected):
    result = validate_tool_result(decode_json('{"content":[],"structuredContent":' + value + "}"))
    assert dumps_wire(result["structuredContent"]) == expected
    assert dumps_wire(validate_tool_result(result)["structuredContent"]) == expected


def test_metadata_root_is_a_map_but_its_children_are_json_values():
    result = validate_tool_result(
        decode_json("""{"content":[],"_meta":{
        "$serde_json::private::Number":"not a number",
        "nested":{"$serde_json::private::Number":"1e999"}}}""")
    )
    assert result["_meta"]["$serde_json::private::Number"] == "not a number"
    assert dumps_wire(result["_meta"]["nested"]) == "1e+999"


@pytest.mark.parametrize("token", ["0.5", "1.0", "1e1", "1e999", "18446744073709551616"])
def test_buffered_number_map_is_not_a_typed_priority_float(token):
    raw = decode_json(
        '{"content":[{"type":"text","text":"ok","annotations":{"priority":' + token + "}}]}"
    )
    with pytest.raises(MCPProtocolError):
        validate_tool_result(raw)


@pytest.mark.parametrize(
    "value",
    [
        '{"$serde_json::private::Number":"PRIVATE"}',
        '{"$serde_json::private::Number":1}',
        '{"$serde_json::private::RawValue":"PRIVATE"}',
        '{"$serde_json::private::Number":"1","other":2}',
    ],
)
def test_invalid_value_marker_becomes_protocol_error_without_reflecting_data(value):
    with pytest.raises(MCPProtocolError) as caught:
        validate_tool_result(decode_json('{"content":[],"structuredContent":' + value + "}"))
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("0.5", "0.5"),
        ("1e999", "1e+999"),
        ("1e-999", "1e-999"),
        ("1.234567890123456789", "1.234567890123456789"),
        ('{"$serde_json::private::Number":"1e999"}', "1e+999"),
        ('{"$serde_json::private::RawValue":"[1e999]"}', "[1e+999]"),
        ('{"$serde_json::private::Number":"PRIVATE"}', None),
        ('{"$serde_json::private::RawValue":"PRIVATE"}', None),
    ],
)
def test_logging_value_does_not_abort_inbound_service(caplog, value, expected):
    service = InboundService({}, None)
    message = decode_json(
        '{"jsonrpc":"2.0","method":"notifications/message",'
        '"params":{"level":"info","data":' + value + "}}"
    )
    with caplog.at_level("INFO", logger="corki.mcp.inbound"):
        assert service.receive(message)
    if expected is None:
        assert not caplog.records
    else:
        assert len(caplog.records) == 1
        assert caplog.records[0].getMessage().endswith(": " + expected)


@pytest.mark.parametrize("token", ["1e999", "1e-999", "1.234567890123456789"])
def test_stdio_reader_preserves_exact_result_after_numeric_log(caplog, token):
    async def scenario():
        pending = {1: asyncio.get_running_loop().create_future()}
        service = InboundService(pending, None)
        reader = asyncio.StreamReader()
        reader.feed_data(
            (
                '{"jsonrpc":"2.0","method":"notifications/message",'
                '"params":{"level":"info","data":' + token + "}}\n"
                '{"jsonrpc":"2.0","id":1,"result":{"content":[],"structuredContent":'
                + token
                + "}}\n"
            ).encode()
        )
        reader.feed_eof()
        with caplog.at_level("INFO", logger="corki.mcp.inbound"):
            await _read_stdio(reader, pending, inbound=service)
        result = validate_tool_result((await pending[1])["result"])
        assert dumps_wire(result["structuredContent"]) == WireNumber(token).token
        assert caplog.records[0].getMessage().endswith(": " + WireNumber(token).token)

    asyncio.run(scenario())
