"""Typed duplicate fields cannot be recovered after a last-key-wins decode."""

import asyncio

import httpx
import pytest

from corki.mcp.client import _decode_http_response, _read_stdio, validate_tool_result
from corki.mcp.json_rpc import MCPProtocolError, decode_json
from corki.protocol.tools import CodeModeOutput


@pytest.mark.parametrize(
    "body",
    [
        '{"content":[],"content":[]}',
        '{"content":[],"isError":true,"isError":false}',
        '{"content":[],"isError":null,"isError":false}',
        '{"content":[],"_meta":null,"_meta":{}}',
        '{"content":[],"structuredContent":null,"structuredContent":0}',
        '{"content":[],"resultType":null,"resultType":"complete"}',
        '{"content":[{"type":"text","type":"text","text":"x"}]}',
        '{"content":[{"type":"text","text":"PRIVATE","text":"x"}]}',
        '{"content":[{"type":"image","data":"x","data":"y","mimeType":"x"}]}',
        '{"content":[{"type":"audio","data":"x","mimeType":"x","mimeType":"y"}]}',
        '{"content":[{"type":"text","text":"x","_meta":null,"_meta":{}}]}',
        '{"content":[{"type":"text","text":"x","annotations":null,"annotations":{}}]}',
        '{"content":[{"type":"text","text":"x","annotations":{"audience":null,"audience":[]}}]}',
        '{"content":[{"type":"text","text":"x","annotations":{"priority":null,"priority":1}}]}',
        '{"content":[{"type":"text","text":"x","annotations":{"lastModified":null,"lastModified":"x"}}]}',
        '{"content":[{"type":"resource","resource":{"uri":"x","uri":"y","text":"x","blob":"y"}}]}',
        '{"content":[{"type":"resource","resource":{"uri":"x","text":"x","text":"y"}}]}',
        '{"content":[{"type":"resource_link","uri":"x","name":"x","name":"y"}]}',
        '{"content":[{"type":"resource_link","uri":"x","name":"x","icons":[{"src":"x","src":"y"}]}]}',
        '{"content":[{"type":"resource_link","uri":"x","name":"x","icons":[{"src":"x","theme":null,"theme":"dark"}]}]}',
    ],
)
def test_duplicate_known_result_fields_reject_the_entire_result(body):
    with pytest.raises(MCPProtocolError) as error:
        validate_tool_result(decode_json(body))
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize(
    "resource,expected",
    [
        (
            '{"uri":"x","text":"first","text":"second","blob":"fallback"}',
            {"uri": "x", "blob": "fallback"},
        ),
        (
            '{"uri":"x","text":"selected","blob":"first","blob":"second"}',
            {"uri": "x", "text": "selected"},
        ),
        ('{"uri":"x","text":false,"blob":"selected"}', {"uri": "x", "blob": "selected"}),
    ],
)
def test_duplicate_variant_field_only_invalidates_that_resource_candidate(resource, expected):
    body = '{"content":[{"type":"resource","resource":' + resource + "}]}"
    assert validate_tool_result(decode_json(body))["content"][0]["resource"] == expected


def test_unknown_and_value_duplicate_keys_are_not_typed_duplicate_fields():
    body = """{"content":[{"type":"text","text":"ok","unknown":1,"unknown":2,
        "_meta":{"custom":1,"custom":2},"annotations":{"unknown":1,"unknown":2}}],
        "unknown":1,"unknown":2,"structuredContent":{"count":1,"count":2}}"""
    result = validate_tool_result(decode_json(body))
    assert result["content"] == [
        {"type": "text", "text": "ok", "_meta": {"custom": 2}, "annotations": {}}
    ]
    assert result["structuredContent"] == {"count": 2}
    assert type(result["structuredContent"]) is dict
    assert type(result["content"][0]["_meta"]) is dict


@pytest.mark.parametrize("transport", ["http", "sse", "stdio"])
@pytest.mark.parametrize("valid", [False, True])
def test_transport_preserves_resource_candidate_field_occurrences(transport, valid):
    resource = '{"uri":"x","text":"PRIVATE","text":"last"' + (
        ',"blob":"selected"}' if valid else "}"
    )
    packet = (
        '{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"resource","resource":'
        + resource
        + "}]}}"
    )
    if transport == "stdio":

        async def read():
            reader = asyncio.StreamReader()
            reader.feed_data(packet.encode() + b"\n")
            reader.feed_eof()
            future = asyncio.get_running_loop().create_future()
            await _read_stdio(reader, {1: future})
            return await future

        message = asyncio.run(read())
    else:
        response = httpx.Response(
            200,
            headers={
                "content-type": "text/event-stream" if transport == "sse" else "application/json"
            },
            content=("data: " + packet + "\n\n") if transport == "sse" else packet,
        )
        message = _decode_http_response(response, expected_id=1)
    if valid:
        assert validate_tool_result(message["result"])["content"][0]["resource"] == {
            "uri": "x",
            "blob": "selected",
        }
    else:
        with pytest.raises(MCPProtocolError):
            validate_tool_result(message["result"])


def test_shadowed_string_is_still_decoded_before_typed_selection():
    with pytest.raises(MCPProtocolError):
        decode_json('{"ignored":"\\ud800","ignored":"ok"}')


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_shadowed_invalid_constant_remains_a_syntax_error(constant):
    with pytest.raises(MCPProtocolError):
        decode_json('{"ignored":' + constant + ',"ignored":null}')


@pytest.mark.parametrize("field", ["_meta", "structuredContent"])
def test_nested_value_duplicates_leave_no_parser_objects_in_public_json(field):
    raw = decode_json('{"content":[],"' + field + '":{"custom":{"x":1,"x":2}}}')
    result = validate_tool_result(raw)
    assert type(result[field]["custom"]) is dict
    assert CodeModeOutput(result).normalized().value == {"content": [], field: {"custom": {"x": 2}}}
