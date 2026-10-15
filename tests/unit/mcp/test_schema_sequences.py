"""Ordinary schema structs accept complete sequences; maps and tags do not."""

import json

import pytest

from corki.mcp.elicitation import standard_request
from corki.mcp.json_rpc import decode_json
from corki.mcp.result_candidates import preceding_tool_result


@pytest.mark.parametrize("root_sequence", [False, True])
@pytest.mark.parametrize(
    "field,expected",
    [
        (["string", None, None, ["a"], "a"], {"type": "string", "enum": ["a"], "default": "a"}),
        (
            ["string", None, None, [["a", "A"]], None],
            {"type": "string", "oneOf": [{"const": "a", "title": "A"}]},
        ),
        (
            ["array", None, None, None, None, ["string", ["a"]], None],
            {"type": "array", "items": {"type": "string", "enum": ["a"]}},
        ),
        (
            ["array", None, None, None, None, [[["a", "A"]]], None],
            {"type": "array", "items": {"anyOf": [{"const": "a", "title": "A"}]}},
        ),
        (
            ["string", None, None, ["a"], ["A"], None],
            {"type": "string", "enum": ["a"], "enumNames": ["A"]},
        ),
        (
            ["string", None, None, 1, None, {"date": None}, None],
            {"type": "string", "minLength": 1, "format": "date"},
        ),
        (["number", None, None, 1, None, None], {"type": "number", "minimum": 1.0}),
        (["integer", None, None, None, None, -1], {"type": "integer", "default": -1}),
        (["boolean", None, None, False], {"type": "boolean", "default": False}),
    ],
)
def test_all_primitive_and_nested_item_sequences_project_before_host(
    field, expected, root_sequence
):
    properties = {"field": field}
    schema = (
        [None, "object", "Title", properties, ["field"], "Description"]
        if root_sequence
        else {
            "type": "object",
            "title": "Title",
            "properties": properties,
            "required": ["field"],
            "description": "Description",
        }
    )
    raw = decode_json(json.dumps({"message": "m", "requestedSchema": schema}))
    result = standard_request(raw)
    assert result == {
        "mode": "form",
        "message": "m",
        "requestedSchema": {
            "type": "object",
            "title": "Title",
            "description": "Description",
            "properties": {"field": expected},
            "required": ["field"],
        },
    }
    assert standard_request(result) == result
    assert raw["requestedSchema"] == schema


@pytest.mark.parametrize(
    "schema",
    [
        [None, "object", None, {}, None],
        [None, "object", None, {}, None, None, None],
        [None, "object", None, [], None, None],
        {"type": "object", "properties": {"x": ["boolean", None, None]}},
        {"type": "object", "properties": {"x": ["boolean", None, None, None, None]}},
        {"type": "object", "properties": {"x": ["array", None, None, None, None, [[[]]], None]}},
    ],
)
def test_missing_optional_slots_extra_slots_and_map_only_properties_reject(schema):
    with pytest.raises(ValueError):
        standard_request({"message": "m", "requestedSchema": schema})


@pytest.mark.parametrize(
    "input_request,valid",
    [
        (["roots/list", None], True),
        (["roots/list"], False),
        (["roots/list", {}, None], False),
        (["roots/list", []], False),
        (
            [
                "sampling/createMessage",
                {
                    "maxTokens": 1,
                    "messages": [["user", {"type": "text", "text": "x"}, None]],
                    "modelPreferences": [[["model"]], 1, None, None],
                    "toolChoice": [{"auto": None}],
                },
            ],
            True,
        ),
        (
            [
                "sampling/createMessage",
                {"maxTokens": 1, "messages": [["user", {"type": "text", "text": "x"}]]},
            ],
            False,
        ),
        (
            [
                "sampling/createMessage",
                {"maxTokens": 1, "messages": [], "modelPreferences": [None, None, None]},
            ],
            False,
        ),
        (["sampling/createMessage", {"maxTokens": 1, "messages": [], "toolChoice": []}], False),
        (
            [
                "elicitation/create",
                {"message": "m", "requestedSchema": [None, "object", None, {}, None, None]},
            ],
            True,
        ),
        (["elicitation/create", ["m", {}]], False),
    ],
)
def test_nested_input_request_proxy_and_sampling_sequences_do_not_execute(input_request, valid):
    value = decode_json(
        json.dumps(
            {
                "content": [],
                "taskId": "t",
                "createdAt": "x",
                "lastUpdatedAt": "y",
                "status": "input_required",
                "inputRequests": {"r": input_request},
            }
        )
    )
    assert preceding_tool_result(value) == ("GetTaskResult" if valid else None)
