"""A result body must actually describe a completed MCP tool result."""

from copy import deepcopy

import pytest

from corki.mcp.client import MCPProtocolError, validate_tool_result


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"unknown": "PRIVATE"},
        {"content": None, "structuredContent": None, "isError": None, "_meta": None},
        {"resultType": "complete"},
        {"resultType": "input_required", "_meta": {}},
        {"resultType": "input_required", "content": []},
        {"resultType": "PRIVATE", "content": []},
        {"resultType": False, "content": []},
        {"resultType": 1, "content": []},
        {"resultType": [], "content": []},
        {"content": [], "_meta": []},
        {"content": [], "_meta": "PRIVATE"},
        {"content": [], "_meta": False},
    ],
)
def test_noncompletion_is_rejected_without_echoing_remote_data(result):
    with pytest.raises(MCPProtocolError) as error:
        validate_tool_result(result)
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("isError", False),
        ("isError", True),
        ("_meta", {}),
        ("structuredContent", 0),
        ("structuredContent", False),
        ("structuredContent", []),
        ("structuredContent", {}),
        ("structuredContent", ""),
    ],
)
@pytest.mark.parametrize("nullable", [False, True])
def test_present_result_field_allows_absent_or_null_content_without_mutating_source(
    field, value, nullable
):
    result = {field: value, "resultType": "complete"}
    if nullable:
        result["content"] = None
    before = deepcopy(result)
    normalized = validate_tool_result(result)
    assert normalized == {**result, "content": []}
    assert result == before


@pytest.mark.parametrize("result_type", [None, "complete"])
def test_explicit_empty_content_is_a_completed_result(result_type):
    result = {"resultType": result_type, "content": [], "isError": None, "_meta": None}
    assert validate_tool_result(result) == result
