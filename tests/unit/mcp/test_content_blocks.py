"""Typed MCP content is validated before capability filtering or plain-text fallback."""

from copy import deepcopy

import pytest

from corki.mcp.client import MCPProtocolError, validate_tool_result


@pytest.mark.parametrize(
    "block",
    [
        None,
        3,
        {},
        {"type": []},
        {"type": "PRIVATE"},
        {"type": "text"},
        {"type": "text", "text": False},
        {"type": "image", "data": "x"},
        {"type": "audio", "data": "x", "mime_type": "audio/wav"},
        {"type": "image", "data": None, "mimeType": "image/png"},
        {"type": "resource", "resource": {"uri": "x"}},
        {"type": "resource", "resource": {"text": "x"}},
        {"type": "resource_link", "uri": "x"},
        {"type": "resource_link", "uri": "x", "name": "x", "size": -1},
        {"type": "resource_link", "uri": "x", "name": "x", "size": True},
        {"type": "resource_link", "uri": "x", "name": "x", "size": 2**64},
        {"type": "text", "text": "x", "_meta": []},
        {"type": "text", "text": "x", "annotations": []},
        {"type": "text", "text": "x", "annotations": {"audience": ["system"]}},
        {"type": "text", "text": "x", "annotations": {"priority": True}},
        {"type": "text", "text": "x", "annotations": {"lastModified": 7}},
        {
            "type": "resource_link",
            "uri": "x",
            "name": "x",
            "icons": [{"src": "x", "theme": "PRIVATE"}],
        },
    ],
)
def test_bad_block_rejects_whole_result_even_with_structured_content(block):
    with pytest.raises(MCPProtocolError) as error:
        validate_tool_result(
            {
                "content": [{"type": "text", "text": "before"}, block],
                "structuredContent": {"ok": True},
            }
        )
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize(
    "block,expected",
    [
        (
            {"type": "text", "text": "x", "extra": "DROP", "_meta": None},
            {"type": "text", "text": "x"},
        ),
        (
            {"type": "image", "data": "not-base64", "mimeType": "anything", "mime_type": "ignored"},
            {"type": "image", "data": "not-base64", "mimeType": "anything"},
        ),
        (
            {"type": "audio", "data": "", "mimeType": ""},
            {"type": "audio", "data": "", "mimeType": ""},
        ),
        (
            {
                "type": "resource",
                "resource": {"uri": "x", "text": "first", "blob": "ignored", "extra": True},
            },
            {"type": "resource", "resource": {"uri": "x", "text": "first"}},
        ),
        (
            {
                "type": "resource",
                "resource": {"uri": "x", "text": False, "blob": "fallback", "mimeType": None},
            },
            {"type": "resource", "resource": {"uri": "x", "blob": "fallback"}},
        ),
        (
            {
                "type": "resource_link",
                "uri": "not a URL",
                "name": "",
                "size": 2**64 - 1,
                "title": None,
                "icons": [{"src": "x", "sizes": ["not-WxH"], "theme": "dark", "extra": 1}],
            },
            {
                "type": "resource_link",
                "uri": "not a URL",
                "name": "",
                "size": 2**64 - 1,
                "icons": [{"src": "x", "sizes": ["not-WxH"], "theme": "dark"}],
            },
        ),
        (
            {
                "type": "text",
                "text": "x",
                "annotations": {
                    "audience": ["user", "assistant"],
                    "priority": 2,
                    "lastModified": "not a date",
                    "extra": 1,
                },
                "_meta": {"custom": {"x": 1}},
            },
            {
                "type": "text",
                "text": "x",
                "annotations": {
                    "audience": ["user", "assistant"],
                    "priority": 2,
                    "lastModified": "not a date",
                },
                "_meta": {"custom": {"x": 1}},
            },
        ),
    ],
)
def test_typed_projection_and_resource_order_without_input_mutation(block, expected):
    before = deepcopy(block)
    result = validate_tool_result({"content": [block]})
    assert result["content"] == [expected]
    assert block == before
