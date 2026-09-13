from copy import deepcopy

import pytest

from corki.mcp.elicitation import standard_request


def normalize(field):
    return standard_request(
        {"message": "Input", "requestedSchema": {"type": "object", "properties": {"field": field}}}
    )["requestedSchema"]["properties"]["field"]


@pytest.mark.parametrize(
    "field,expected",
    [
        ({"type": "string", "title": None, "default": None, "minLength": None}, {"type": "string"}),
        (
            {"type": "string", "pattern": "ignored", "minLength": 2},
            {"type": "string", "minLength": 2},
        ),
        ({"type": "number", "minimum": 1, "maximum": None}, {"type": "number", "minimum": 1.0}),
        ({"type": "integer", "default": -5, "enum": [1, 2]}, {"type": "integer", "default": -5}),
        (
            {"type": "boolean", "default": False, "unknown": True},
            {"type": "boolean", "default": False},
        ),
        ({"type": "string", "enum": ["a", "b"]}, {"type": "string", "enum": ["a", "b"]}),
        (
            {"type": "string", "enum": ["a"], "enumNames": ["A"], "minLength": 3},
            {"type": "string", "enum": ["a"], "enumNames": ["A"]},
        ),
        (
            {
                "type": "string",
                "enum": ["a"],
                "oneOf": [{"const": "b", "title": "B", "ignored": 1}],
            },
            {"type": "string", "oneOf": [{"const": "b", "title": "B"}]},
        ),
        ({"type": "string", "enum": [1]}, {"type": "string"}),
        (
            {"type": "string", "oneOf": [{"const": "a"}], "maxLength": 4},
            {"type": "string", "maxLength": 4},
        ),
        ({"type": "string", "enum": ["a"], "format": "unknown"}, {"type": "string", "enum": ["a"]}),
        (
            {
                "type": "array",
                "items": {"type": "string", "enum": ["a"], "ignored": 1},
                "default": None,
            },
            {"type": "array", "items": {"type": "string", "enum": ["a"]}},
        ),
        (
            {"type": "array", "items": {"oneOf": [{"const": "a", "title": "A"}]}, "minItems": 0},
            {"type": "array", "items": {"anyOf": [{"const": "a", "title": "A"}]}, "minItems": 0},
        ),
        (
            {"type": "integer", "minimum": 10, "maximum": 1, "default": 99},
            {"type": "integer", "minimum": 10, "maximum": 1, "default": 99},
        ),
    ],
)
def test_reference_variant_normalization_and_immutability(field, expected):
    original = deepcopy(field)
    assert normalize(field) == expected
    assert field == original


@pytest.mark.parametrize(
    "field",
    [
        None,
        [],
        {},
        {"type": "object"},
        {"type": "string", "minLength": -1},
        {"type": "string", "minLength": 2**32},
        {"type": "string", "minLength": True},
        {"type": "string", "title": 1},
        {"type": "string", "format": "password"},
        {"type": "string", "default": 1},
        {"type": "number", "minimum": True},
        {"type": "number", "default": "1"},
        {"type": "integer", "minimum": 1.0},
        {"type": "integer", "default": 2**63},
        {"type": "integer", "minimum": -(2**63) - 1},
        {"type": "boolean", "default": 0},
        {"type": "array", "items": {"type": "string"}},
        {"type": "array", "items": {"anyOf": [{"const": "a", "title": 1}]}},
        {"type": "array", "items": {"type": "string", "enum": ["a"]}, "maxItems": 2**64},
    ],
)
def test_invalid_typed_schema_rejected_before_host(field):
    with pytest.raises(ValueError):
        normalize(field)


def test_root_optional_nulls_unknown_fields_and_property_order():
    original = {
        "message": "Input",
        "requestedSchema": {
            "type": "object",
            "properties": {"z": {"type": "string"}, "a": {"type": "boolean"}},
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "required": None,
            "title": None,
            "description": None,
            "additionalProperties": False,
        },
    }
    result = standard_request(original)["requestedSchema"]
    assert list(result["properties"]) == ["z", "a"]
    assert result == {
        "type": "object",
        "properties": original["requestedSchema"]["properties"],
        "$schema": "https://json-schema.org/draft/2020-12/schema",
    }
    assert "required" in original["requestedSchema"]


@pytest.mark.parametrize(
    "change",
    [{"required": "field"}, {"required": [1]}, {"$schema": 2}, {"title": []}, {"properties": None}],
)
def test_invalid_root_fields(change):
    with pytest.raises(ValueError):
        standard_request(
            {"message": "Input", "requestedSchema": {"type": "object", "properties": {}, **change}}
        )


@pytest.mark.parametrize(
    "field,expected",
    [
        ({"type": "string", "minLength": 2**32 - 1}, {"type": "string", "minLength": 2**32 - 1}),
        (
            {"type": "integer", "default": -(2**63), "maximum": 2**63 - 1},
            {"type": "integer", "default": -(2**63), "maximum": 2**63 - 1},
        ),
        (
            {"type": "array", "maxItems": 2**64 - 1, "items": {"type": "string", "enum": []}},
            {"type": "array", "maxItems": 2**64 - 1, "items": {"type": "string", "enum": []}},
        ),
        (
            {
                "type": "array",
                "items": {"type": "string", "enum": ["a"], "anyOf": [{"const": "b", "title": "B"}]},
            },
            {"type": "array", "items": {"type": "string", "enum": ["a"]}},
        ),
        (
            {"type": "string", "enum": ["a"], "enumNames": 4, "minLength": 1},
            {"type": "string", "minLength": 1},
        ),
    ],
)
def test_type_boundaries_and_first_matching_nested_variant(field, expected):
    assert normalize(field) == expected


@pytest.mark.parametrize("case", ["scalar", "nested", "duplicate_alias", "null_properties"])
def test_invalid_schema_returns_invalid_params_without_host_request(case):
    import asyncio

    from corki.mcp.elicitation import ElicitationRouter
    from corki.mcp.inbound import InboundService

    async def scenario():
        delivered, replies = [], []

        async def host(request):
            delivered.append(request)

        async def send(reply):
            replies.append(reply)

        field = {
            "scalar": {"type": "integer", "default": False},
            "nested": {"type": "object", "properties": {}},
            "duplicate_alias": {"type": "array", "items": {"anyOf": [], "oneOf": []}},
            "null_properties": {"type": "string"},
        }[case]
        schema = {"type": "object", "properties": {"field": field}}
        if case == "null_properties":
            schema["properties"] = None
        service = InboundService({}, send, elicitations=ElicitationRouter(host))
        try:
            service.receive(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "elicitation/create",
                    "params": {"message": "Input", "requestedSchema": schema},
                }
            )
            await asyncio.wait_for(asyncio.gather(*service._tasks), 1)
            assert not delivered
            assert replies == [
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "error": {"code": -32602, "message": "Invalid elicitation parameters"},
                }
            ]
        finally:
            await service.aclose()

    asyncio.run(scenario())
