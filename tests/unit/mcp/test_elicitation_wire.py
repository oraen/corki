"""Raw schema pairs and tagged/legacy elicitation branch selection."""

import asyncio

import pytest

from corki.mcp.elicitation import ElicitationRouter, standard_request
from corki.mcp.elicitation_schema import normalize_schema
from corki.mcp.inbound import InboundService
from corki.mcp.json_rpc import decode_json


@pytest.mark.parametrize(
    "body",
    [
        '{"type":"object","type":"object","properties":{}}',
        '{"type":"object","properties":{},"title":null,"title":"x"}',
        '{"type":"object","properties":{"x":{},"x":{"type":"string"}}}',
        '{"type":"object","properties":{"x":{"type":"boolean","default":null,"default":true}}}',
        '{"type":"object","properties":{"x":{"type":"array","items":{"oneOf":[],"oneOf":[]}}}}',
    ],
)
def test_raw_schema_cannot_erase_invalid_or_duplicate_typed_fields(body):
    with pytest.raises(ValueError):
        normalize_schema(decode_json(body))


def test_duplicate_enum_only_invalidates_candidates_reading_enum():
    body = '{"type":"object","properties":{"x":{"type":"string","enum":[],"enum":[]}}}'
    assert normalize_schema(decode_json(body))["properties"]["x"] == {"type": "string"}


def test_duplicate_valid_properties_keep_first_order_and_last_value():
    body = (
        '{"type":"object","properties":{"x":{"type":"string"},'
        '"y":{"type":"boolean"},"x":{"type":"integer"}}}'
    )
    result = normalize_schema(decode_json(body))["properties"]
    assert list(result) == ["x", "y"] and result["x"] == {"type": "integer"}


@pytest.mark.parametrize("mode", ['"unknown"', "null", "1", '"url"'])
def test_invalid_tagged_candidate_can_still_be_legacy_form(mode):
    result = standard_request(
        decode_json(
            '{"message":"m","mode":'
            + mode
            + ',"requestedSchema":{"type":"object","properties":{}}}'
        )
    )
    assert result["mode"] == "form"


@pytest.mark.parametrize(
    "fields",
    [
        '"message":"m","message":"m","requestedSchema":{"type":"object","properties":{}}',
        '"message":"m","mode":"url","url":"u","url":"u","elicitationId":"e"',
    ],
)
def test_duplicate_known_request_fields_cannot_reach_host(fields):
    with pytest.raises(ValueError):
        standard_request(decode_json("{" + fields + "}"))


@pytest.mark.parametrize("valid", [False, True])
@pytest.mark.parametrize("enum_format", [False, True])
@pytest.mark.parametrize("sequence_schema", [False, True])
def test_raw_elicitation_reaches_host_only_after_typed_candidate_selection(
    valid, enum_format, sequence_schema
):
    async def scenario():
        delivered, replies = [], []

        async def host(request):
            delivered.append(request)
            assert request.params["mode"] == "form"
            if enum_format:
                assert request.params["requestedSchema"]["properties"] == {
                    "x": {"type": "string", "format": "date-time"}
                }
            router.respond(request.server_name, request.request_id, "accept", content={})

        async def send(message):
            replies.append(message)

        router = ElicitationRouter(host)
        service = InboundService({}, send, elicitations=router)
        schema = (
            '{"type":"object","properties":{}}'
            if valid
            else ('{"type":"object","properties":{"x":{},"x":{"type":"string"}}}')
        )
        if enum_format:
            value = '{"date-time":null}' if valid else '{"date-time":null,"date-time":null}'
            schema = '{"type":"object","properties":{"x":{"type":"string","format":' + value + "}}}"
        if sequence_schema:
            # Keep duplicate nested pairs intact when changing the root shape.
            properties = schema[len('{"type":"object","properties":') : -1]
            schema = '[null,"object",null,' + properties + ",null,null]"
        try:
            service.receive(
                decode_json(
                    '{"jsonrpc":"2.0","id":7,"method":"elicitation/create",'
                    '"params":{"mode":"unknown","message":"m","requestedSchema":' + schema + "}}"
                )
            )
            await asyncio.wait_for(asyncio.gather(*service._tasks), 1)
            assert bool(delivered) is valid and len(replies) == 1
            if valid:
                assert replies[0]["result"]["action"] == "accept"
            else:
                assert replies[0]["error"]["code"] == -32602
        finally:
            await service.aclose()

    asyncio.run(scenario())
