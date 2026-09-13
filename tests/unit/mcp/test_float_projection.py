"""RMCP's typed f32 is rounded and serialized before becoming a public Value."""

from copy import deepcopy

import pytest

from corki.mcp.client import validate_tool_result
from corki.mcp.json_rpc import MCPProtocolError, decode_json
from corki.protocol.wire_numbers import dumps_wire, loads_number_values


@pytest.mark.parametrize("sequence", [False, True])
@pytest.mark.parametrize(
    "integer,expected",
    [
        (0, "0.0"),
        (1, "1.0"),
        (-1, "-1.0"),
        (16777217, "16777216.0"),
        (16777219, "16777220.0"),
        (134217800, "134217800.0"),
        (134217810, "134217810.0"),
        (10**13, "1e+13"),
        (2**63 - 1, "9.223372e+18"),
        (2**63 + 2**39 + 1, "9.223373e+18"),
        (-(2**63), "-9.223372e+18"),
        (2**64 - 1, "1.8446744e+19"),
    ],
)
def test_raw_integer_priority_is_projected_and_survives_public_revalidation(
    integer, expected, sequence
):
    annotation = f"[null,{integer},null]" if sequence else f'{{"priority":{integer}}}'
    raw = decode_json('{"content":[{"type":"text","text":"ok","annotations":' + annotation + "}]}")
    before = deepcopy(raw)
    result = validate_tool_result(raw)
    encoded = dumps_wire(result)
    assert f'"priority":{expected}' in encoded
    assert raw == before
    assert dumps_wire(validate_tool_result(result)) == encoded
    restored = loads_number_values(encoded)
    assert dumps_wire(validate_tool_result(restored)) == encoded


@pytest.mark.parametrize(
    "token", ["0.5", "1.0", "1e0", "1e+13", "18446744073709551616", "-9223372036854775809"]
)
def test_raw_arbitrary_precision_number_does_not_become_a_typed_float(token):
    raw = decode_json(
        '{"content":[{"type":"text","text":"ok","annotations":{"priority":' + token + "}}]}"
    )
    with pytest.raises(MCPProtocolError):
        validate_tool_result(raw)


@pytest.mark.parametrize(
    "fields",
    [
        '"type":"text","text":"x"',
        '"type":"image","data":"x","mimeType":"image/png"',
        '"type":"audio","data":"x","mimeType":"audio/wav"',
        '"type":"resource","resource":{"uri":"urn:x","text":"x"}',
        '"type":"resource_link","uri":"urn:x","name":"x"',
    ],
)
def test_all_content_variants_share_the_typed_float_projection(fields):
    raw = decode_json('{"content":[{' + fields + ',"annotations":{"priority":16777217}}]}')
    result = validate_tool_result(raw)
    assert dumps_wire(result["content"][0]["annotations"]) == '{"priority":16777216.0}'


@pytest.mark.parametrize("first", ["null", "1", "0.5"])
def test_rounding_does_not_erase_duplicate_typed_fields(first):
    raw = decode_json(
        '{"content":[{"type":"text","text":"x","annotations":{"priority":'
        + first
        + ',"priority":1}}]}'
    )
    with pytest.raises(MCPProtocolError):
        validate_tool_result(raw)
