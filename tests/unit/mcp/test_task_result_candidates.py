"""Task-result matching includes optional payloads and typed nested requests."""

import json

import pytest

from corki.mcp.client import validate_tool_result
from corki.mcp.json_rpc import MCPProtocolError, decode_json
from corki.mcp.result_candidates import preceding_tool_result


def task(fields):
    return decode_json(
        '{"content":[],"taskId":"t","createdAt":"not-a-date",'
        '"lastUpdatedAt":"also-not-a-date",' + fields + "}"
    )


@pytest.mark.parametrize(
    "fields,valid",
    [
        ('"status":"working"', True),
        ('"status":{"working":null}', True),
        ('"status":{"working":0}', False),
        ('"status":{"working":null,"working":null}', False),
        ('"status":"cancelled"', True),
        ('"status":"completed","result":{}', True),
        ('"status":"failed","error":{}', True),
        ('"status":"input_required","inputRequests":{}', True),
        ('"status":"completed"', False),
        ('"status":"completed","result":null', False),
        ('"status":"failed","error":null', False),
        ('"status":"input_required","inputRequests":null', False),
        ('"status":"unknown"', False),
        ('"status":"working","result":[]', False),
        ('"status":"working","error":[]', False),
        ('"status":"cancelled","inputRequests":[]', False),
        ('"status":"working","resultType":null', False),
        ('"status":"working","resultType":{"complete":null}', False),
        ('"status":"working","ttlMs":-1', False),
        ('"status":"working","ttlMs":1.0', False),
        ('"status":"working","ttlMs":18446744073709551615', True),
        ('"status":"working","pollIntervalMs":null', True),
        ('"status":"working","pollIntervalMs":true', False),
        ('"status":"working","status":"working"', False),
        ('"status":"working","result":null,"result":{}', False),
        ('"status":"working","result":{"x":1,"x":2}', True),
        ('"status":"working","result":{"x":{"$serde_json::private::Number":"bad"}}', False),
    ],
)
def test_detailed_task_state_and_all_present_payload_fields(fields, valid):
    raw = task(fields)
    assert preceding_tool_result(raw) == ("GetTaskResult" if valid else None)
    if valid:
        with pytest.raises(MCPProtocolError, match="unexpected response type"):
            validate_tool_result(raw)
    elif isinstance(raw.get("resultType"), dict):
        # This field is invalid for both candidates, not a valid tool fallback.
        with pytest.raises(MCPProtocolError, match="did not return a complete result"):
            validate_tool_result(raw)
    else:
        assert validate_tool_result(raw)["content"] == []


@pytest.mark.parametrize(
    "input_request,valid",
    [
        ({"method": "roots/list"}, True),
        ({"method": "roots/list", "params": None}, True),
        ({"method": "roots/list", "params": {}}, True),
        ({"method": "roots/list", "params": 1}, False),
        ({"method": "roots/list", "params": {"_meta": []}}, False),
        ({"method": "ping"}, False),
        ({"method": "custom"}, False),
        ({"method": {"roots/list": None}}, False),
        ({"method": "sampling/createMessage", "params": {"messages": [], "maxTokens": 0}}, True),
        ({"method": "sampling/createMessage", "params": {"messages": []}}, False),
        (
            {
                "method": "sampling/createMessage",
                "params": {"messages": [], "maxTokens": 1, "temperature": 0.5},
            },
            False,
        ),
        (
            {
                "method": "sampling/createMessage",
                "params": {
                    "messages": [],
                    "maxTokens": 1,
                    "temperature": 2,
                    "includeContext": "thisServer",
                    "toolChoice": {"mode": "auto"},
                },
            },
            True,
        ),
        ({"method": "sampling/createMessage", "params": {"messages": [], "maxTokens": -1}}, False),
        (
            {
                "method": "sampling/createMessage",
                "params": {
                    "messages": [],
                    "maxTokens": 1,
                    "tools": [{"name": "t", "inputSchema": {}}],
                },
            },
            True,
        ),
        (
            {
                "method": "sampling/createMessage",
                "params": {
                    "messages": [],
                    "maxTokens": 1,
                    "modelPreferences": {"hints": [{}], "speedPriority": 1},
                },
            },
            True,
        ),
        (
            {
                "method": "sampling/createMessage",
                "params": {
                    "messages": [
                        {
                            "role": "user",
                            "content": {"type": "tool_use", "id": "x", "name": "n", "input": {}},
                        }
                    ],
                    "maxTokens": 1,
                },
            },
            True,
        ),
        (
            {
                "method": "sampling/createMessage",
                "params": {
                    "messages": [
                        {
                            "role": "assistant",
                            "content": [{"type": "tool_result", "toolUseId": "x", "content": []}],
                        }
                    ],
                    "maxTokens": 1,
                },
            },
            True,
        ),
        (
            {
                "method": "sampling/createMessage",
                "params": {
                    "messages": [
                        {
                            "role": "user",
                            "content": {"type": "resource_link", "uri": "u", "name": "n"},
                        }
                    ],
                    "maxTokens": 1,
                },
            },
            False,
        ),
        (
            {
                "method": "elicitation/create",
                "params": {"message": "m", "requestedSchema": {"type": "object", "properties": {}}},
            },
            True,
        ),
        (
            {
                "method": "elicitation/create",
                "params": {
                    "message": "m",
                    "mode": "unknown",
                    "requestedSchema": {"type": "object", "properties": {}},
                },
            },
            True,
        ),
        (
            {
                "method": "elicitation/create",
                "params": {"message": "m", "mode": "url", "url": "anything", "elicitationId": "x"},
            },
            True,
        ),
        (
            {
                "method": "elicitation/create",
                "params": {
                    "message": "m",
                    "mode": "url",
                    "url": False,
                    "requestedSchema": {"type": "object", "properties": {}},
                },
            },
            True,
        ),
        ({"method": "elicitation/create", "params": {"message": "m", "mode": "unknown"}}, False),
    ],
)
def test_input_request_union_is_typed_but_does_not_run_semantic_validation(input_request, valid):
    raw = task('"status":"input_required","inputRequests":' + json.dumps({"r": input_request}))
    assert preceding_tool_result(raw) == ("GetTaskResult" if valid else None)


@pytest.mark.parametrize(
    "requests",
    [
        '{"r":{"method":"ping"},"r":{"method":"roots/list"}}',
        '{"r":{"method":"roots/list","params":null,"params":{}}}',
        '{"r":{"method":"roots/list","method":"roots/list"}}',
        '{"r":{"method":"sampling/createMessage","params":{"messages":[],"maxTokens":1,"maxTokens":2}}}',
    ],
)
def test_every_raw_input_request_occurrence_is_validated(requests):
    assert preceding_tool_result(task('"status":"working","inputRequests":' + requests)) is None
