"""Buffered unit enums decode either spelling but re-encode as strings."""

import json

import pytest

from corki.mcp.client import validate_tool_result
from corki.mcp.elicitation_schema import normalize_schema
from corki.mcp.json_rpc import MCPProtocolError, decode_json
from corki.mcp.result_candidates import preceding_tool_result


def project(kind, encoded):
    value = decode_json(encoded)
    if kind == "format":
        schema = {"type": "object", "properties": {"x": {"type": "string", "format": value}}}
        return normalize_schema(schema)["properties"]["x"]["format"]
    if kind == "role":
        block = {"type": "text", "text": "x", "annotations": {"audience": [value]}}
        return validate_tool_result({"content": [block]})["content"][0]["annotations"]["audience"][
            0
        ]
    block = {
        "type": "resource_link",
        "uri": "u",
        "name": "n",
        "icons": [{"src": "s", "theme": value}],
    }
    return validate_tool_result({"content": [block]})["content"][0]["icons"][0]["theme"]


@pytest.mark.parametrize(
    "kind,name",
    [("role", "user"), ("role", "assistant"), ("theme", "light"), ("theme", "dark")]
    + [("format", name) for name in ("email", "uri", "date", "date-time")],
)
@pytest.mark.parametrize("mapped", [False, True])
def test_unit_enum_public_projection_is_canonical_and_idempotent(kind, name, mapped):
    encoded = json.dumps({name: None} if mapped else name)
    assert project(kind, encoded) == name
    assert project(kind, json.dumps(project(kind, encoded))) == name


@pytest.mark.parametrize("kind,name", [("role", "user"), ("theme", "light"), ("format", "date")])
@pytest.mark.parametrize(
    "template",
    [
        "{}",
        '{"NAME":false}',
        '{"NAME":[]}',
        '{"NAME":{}}',
        '{"NAME":null,"NAME":null}',
        '{"NAME":null,"PRIVATE":null}',
        '{"PRIVATE":null}',
        '["NAME"]',
        "true",
    ],
)
def test_invalid_unit_enum_does_not_erase_duplicates_or_reflect_remote_values(kind, name, template):
    with pytest.raises((MCPProtocolError, ValueError)) as error:
        project(kind, template.replace("NAME", name))
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize("duplicate", [False, True])
def test_icon_enum_affects_preceding_candidate_without_mutating_fallback(duplicate):
    theme = '{"light":null,"light":null}' if duplicate else '{"light":null}'
    result = decode_json(
        '{"content":[],"tools":[{"name":"x","inputSchema":{},'
        '"icons":[{"src":"s","theme":' + theme + "}]}]}"
    )
    assert preceding_tool_result(result) == (None if duplicate else "ListToolsResult")
    if duplicate:
        assert validate_tool_result(result)["content"] == []
    else:
        with pytest.raises(MCPProtocolError, match="unexpected response"):
            validate_tool_result(result)


def test_internal_content_tag_is_not_a_unit_enum():
    with pytest.raises(MCPProtocolError):
        validate_tool_result(decode_json('{"content":[{"type":{"text":null},"text":"x"}]}'))
