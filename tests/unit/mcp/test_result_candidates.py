"""Raw untagged results select a valid earlier candidate, not a field name."""

import asyncio
import json

import httpx
import pytest

from corki.mcp.client import _decode_http_response, _read_stdio, validate_tool_result
from corki.mcp.json_rpc import MCPProtocolError, decode_json


@pytest.mark.parametrize(
    "earlier",
    [
        {
            "resultType": "complete",
            "supportedVersions": [],
            "capabilities": {},
            "ttlMs": 0,
            "cacheScope": "private",
        },
        {
            "protocolVersion": "future",
            "capabilities": {},
            "serverInfo": {"name": "n", "version": "v"},
        },
        {"completion": {"values": []}},
        {"messages": []},
        {"prompts": []},
        {"resources": []},
        {"resourceTemplates": []},
        {"contents": []},
        {"tools": []},
        {"action": "accept"},
        {"resultType": "complete", "_meta": {"io.modelcontextprotocol/subscriptionId": 1}},
        {"tools": [{"name": "n", "inputSchema": {}}]},
        {"prompts": [{"name": "n", "arguments": [{"name": "a", "required": False}]}]},
        {"resources": [{"name": "n", "uri": "u", "annotations": {"priority": 1}}]},
        {"resourceTemplates": [{"name": "n", "uriTemplate": "{x}"}]},
        {"contents": [{"uri": "u", "text": False, "blob": "fallback"}]},
        {"messages": [{"role": "user", "content": {"type": "text", "text": "x"}}]},
        {"completion": {"values": ["v"] * 101}},
        {"tools": [], "ttlMs": -1},
    ],
)
def test_valid_earlier_result_is_not_a_completed_tool_result(earlier):
    raw = decode_json(json.dumps({"content": [], "private": "PRIVATE", **earlier}))
    with pytest.raises(MCPProtocolError) as caught:
        validate_tool_result(raw)
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize(
    "fields",
    [
        '"tools":false',
        '"tools":[{}]',
        '"tools":[],"tools":[]',
        '"tools":[],"ttlMs":1.0',
        '"tools":[],"ttlMs":9223372036854775808',
        '"tools":[],"cacheScope":"unknown"',
        '"tools":[],"nextCursor":1',
        '"tools":[{"name":"x","inputSchema":{},"annotations":{"readOnlyHint":0}}]',
        '"tools":[{"name":"x","inputSchema":{},"name":"y"}]',
        '"tools":[{"name":"x","inputSchema":[],"icons":[]}]',
        '"completion":{"values":[],"total":4294967296}',
        '"messages":[{"role":"system","content":{"type":"text","text":"x"}}]',
        '"resources":[{"uri":"u","name":"n","annotations":{"priority":0.5}}]',
        '"resourceTemplates":[{"uriTemplate":"u"}]',
        '"contents":[{"uri":"u","text":false}]',
        '"prompts":[{"name":"n","arguments":[{"name":"a","required":1}]}]',
        '"action":"invalid"',
        '"protocolVersion":"x","capabilities":{"tools":{"listChanged":0}},'
        '"serverInfo":{"name":"n","version":"v"}',
        '"resultType":"complete","_meta":{"io.modelcontextprotocol/subscriptionId":1.0}',
        '"supportedVersions":[],"capabilities":{},"ttlMs":0,"cacheScope":"private"',
    ],
)
def test_invalid_earlier_candidate_does_not_shadow_valid_tool_result(fields):
    result = validate_tool_result(decode_json('{"content":[],' + fields + "}"))
    assert result["content"] == []


def test_public_result_revalidation_does_not_repeat_raw_union_selection():
    result = {"content": [], "tools": []}
    assert validate_tool_result(result)["content"] == []


def test_valid_candidate_after_invalid_candidate_still_shadows_call_tool():
    with pytest.raises(MCPProtocolError):
        validate_tool_result(decode_json('{"content":[],"resources":false,"tools":[]}'))


@pytest.mark.parametrize("carrier", ["http", "sse", "stdio"])
@pytest.mark.parametrize("valid", [False, True])
@pytest.mark.parametrize("sequence", [False, True])
def test_candidate_selection_uses_raw_carrier_not_public_result_shape(carrier, valid, sequence):
    body = '{"content":[],"tools":' + ("[]" if valid else "[{}]") + "}"
    if sequence:
        body = "[null,[[],null,null],null]" if valid else "[null,[],null,null,null]"
    packet = '{"jsonrpc":"2.0","id":1,"result":' + body + "}"
    if carrier == "stdio":

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
                "content-type": "application/json" if carrier == "http" else "text/event-stream"
            },
            content=packet if carrier == "http" else "data: " + packet + "\n\n",
        )
        message = _decode_http_response(response, expected_id=1)
    if valid:
        with pytest.raises(MCPProtocolError):
            validate_tool_result(message["result"])
    else:
        assert validate_tool_result(message["result"])["content"] == []
