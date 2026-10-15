"""RMCP struct sequences retain declaration order, arity and candidate precedence."""

import json

import pytest

from corki.mcp.client import validate_tool_result
from corki.mcp.json_rpc import MCPProtocolError, decode_json
from corki.mcp.result_candidates import preceding_tool_result


def wire(value):
    return decode_json(json.dumps(value))


@pytest.mark.parametrize(
    "value,expected",
    [
        (["complete", [], {}, None, 0, "private", None], "DiscoverResult"),
        (["v", {}, {"name": "n", "version": "v"}, None, None], "InitializeResult"),
        ([None, [[], None, None], None], "CompleteResult"),
        ([None, None, [], None], "GetPromptResult"),
        (
            [None, None, None, None, None, [["p", None, None, None, None, None]]],
            "ListPromptsResult",
        ),
        (
            [None, None, None, None, None, [["u", "n", None, None, None, None, None, None, None]]],
            "ListResourcesResult",
        ),
        (
            [None, None, None, None, None, [["u", "n", None, None, None, None, None, None]]],
            "ListResourceTemplatesResult",
        ),
        ([None, None, None, [{"uri": "u", "text": "x"}], None], "ReadResourceResult"),
        (["complete", {"io.modelcontextprotocol/subscriptionId": 1}], "SubscriptionsListenResult"),
        (
            [None, None, None, None, None, [["n", None, None, {}, None, None, None, None]]],
            "ListToolsResult",
        ),
        (["accept", None, None], "ElicitResult"),
    ],
)
def test_root_struct_sequences_select_the_first_matching_result(value, expected):
    raw = wire(value)
    assert preceding_tool_result(raw) == expected
    with pytest.raises(MCPProtocolError, match="unexpected response"):
        validate_tool_result(raw)
    assert raw == value


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("completion", [[], None, None], "CompleteResult"),
        ("messages", [["user", {"type": "text", "text": "x"}]], "GetPromptResult"),
        ("tools", [["n", None, None, {}, None, None, None, None]], "ListToolsResult"),
        ("tools", [{"name": "n", "inputSchema": {}, "annotations": [None] * 5}], "ListToolsResult"),
        ("prompts", [{"name": "n", "arguments": [["a", None, None, False]]}], "ListPromptsResult"),
        (
            "resources",
            [["u", "n", None, None, None, None, None, None, None]],
            "ListResourcesResult",
        ),
        (
            "resourceTemplates",
            [["u", "n", None, None, None, None, None, None]],
            "ListResourceTemplatesResult",
        ),
    ],
)
@pytest.mark.parametrize("malformed", [False, True])
def test_nested_sequences_change_candidate_selection_not_just_root_admission(
    field, value, expected, malformed
):
    if malformed:
        if field == "completion":
            value = value[:-1]
        elif isinstance(value[0], list):
            value = [value[0][:-1]]
        else:
            value = [{}]
    raw = wire({"content": [], field: value})
    assert preceding_tool_result(raw) == (None if malformed else expected)
    if malformed:
        assert validate_tool_result(raw)["content"] == []
    else:
        with pytest.raises(MCPProtocolError):
            validate_tool_result(raw)


@pytest.mark.parametrize("extra", [-1, 0, 1])
def test_call_tool_sequence_requires_every_slot_and_reencodes_as_object(extra):
    value = [None, [{"type": "text", "text": "x"}], None, False, None]
    if extra == -1:
        value.pop()
    elif extra == 1:
        value.append(None)
    if extra:
        with pytest.raises(MCPProtocolError):
            validate_tool_result(wire(value))
    else:
        result = validate_tool_result(wire(value))
        assert result["content"] == [{"type": "text", "text": "x"}]
        assert result["isError"] is False
        assert validate_tool_result(result) == result


@pytest.mark.parametrize(
    "body",
    [
        "[null,[],null,null,null]",
        '{"content":[{"type":"text","text":"x","annotations":[[{"user":null}],null,"date"]}]}',
        '{"content":[{"type":"resource_link","uri":"u","name":"n","icons":[["s",null,null,{"dark":null}]]}]}',
    ],
)
def test_nested_content_structs_project_canonical_json(body):
    result = validate_tool_result(decode_json(body))
    assert validate_tool_result(result) == result
    if result["content"]:
        block = result["content"][0]
        if block["type"] == "text":
            assert block["annotations"] == {"audience": ["user"], "lastModified": "date"}
        else:
            assert block["icons"] == [{"src": "s", "theme": "dark"}]


@pytest.mark.parametrize(
    "value",
    [
        [None, None, None, None, None],
        ["task", [], None, False, None],
        ["resultType", [], None, False, None],
        {"content": [["text", "x", None, None]]},
        {"content": [{"type": "resource", "resource": ["u", None, "x", None]}]},
        {"content": [{"type": "text", "text": "x", "_meta": []}]},
    ],
)
def test_arrays_do_not_bypass_tags_map_only_types_or_result_contract(value):
    with pytest.raises(MCPProtocolError):
        validate_tool_result(wire(value))


@pytest.mark.parametrize("malformed", [False, True])
@pytest.mark.parametrize(
    "part", ["implementation", "capabilities", "tools", "prompts", "resources"]
)
def test_initialize_candidate_nested_structs_keep_their_own_field_order(part, malformed):
    value = {
        "content": [],
        "protocolVersion": "v",
        "capabilities": {},
        "serverInfo": {"name": "n", "version": "v"},
    }
    sequences = {
        "implementation": ["n", None, "v", None, [["s", None, None, "dark"]], None],
        "capabilities": [None, None, None, None, [True], [False, True], [False]],
        "tools": [True],
        "prompts": [False],
        "resources": [False, True],
    }
    sequence = sequences[part]
    if malformed:
        sequence = sequence[:-1]
    if part == "implementation":
        value["serverInfo"] = sequence
    elif part == "capabilities":
        value["capabilities"] = sequence
    else:
        value["capabilities"][part] = sequence
    assert preceding_tool_result(wire(value)) == (None if malformed else "InitializeResult")


def test_flattened_task_is_not_made_into_a_positional_struct():
    value = ["task", "id", "working", "created", "updated", None, None, None]
    assert preceding_tool_result(wire(value)) is None


def test_sequence_projection_preserves_invalid_shadowed_metadata_value():
    body = '[null,[],null,null,{"x":{"$serde_json::private::Number":"bad"},"x":1}]'
    with pytest.raises(MCPProtocolError):
        validate_tool_result(decode_json(body))
