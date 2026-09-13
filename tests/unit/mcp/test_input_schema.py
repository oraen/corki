"""Native MCP normalization and Agent serialized-size policy."""

import json

import pytest

from corki.mcp.input_schema import normalized_input_schema
from corki.mcp.names import MCPToolName
from corki.mcp.tools import MCPTool


def tool(schema, *, agent=False, name="read", description=""):
    return MCPTool(
        "docs",
        {"name": name, "description": description, "inputSchema": schema},
        object(),
        agent_plugin=agent,
    )


def test_mcp_schema_infers_types_and_fills_children_without_mutating_source():
    raw = {"properties": {"flag": True, "values": {"type": "array"}}}
    assert tool(raw).spec.parameters == {
        "type": "object",
        "properties": {
            "flag": {"type": "string"},
            "values": {"type": "array", "items": {"type": "string"}},
        },
    }
    assert raw == {"properties": {"flag": True, "values": {"type": "array"}}}


@pytest.mark.parametrize("agent", [False, True])
def test_only_agent_uses_open_object_fallback_after_best_effort_compaction(agent):
    schema = {
        "type": "object",
        "properties": {f"property_{index}": {"type": "string"} for index in range(1024)},
    }
    spec = tool(schema, agent=agent).spec
    assert spec.parameters == (
        {"type": "object", "properties": {}, "additionalProperties": True} if agent else schema
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        (True, {"type": "string"}),
        (False, {"type": "string"}),
        ({"title": "ignored"}, {}),
        ({"type": "invalid"}, {}),
        ({"minimum": 1}, {"type": "number"}),
        ({"format": "date"}, {"type": "string"}),
        (
            {"const": {"description": "instance"}},
            {"type": "string", "enum": [{"description": "instance"}]},
        ),
        ({"type": ["object", "null", "bad", 5]}, {"type": ["object", "null"], "properties": {}}),
        ({"type": ["string", "string"]}, {"type": ["string", "string"]}),
        ({"type": ["array"]}, {"type": "array", "items": {"type": "string"}}),
        ({"prefixItems": [False]}, {"type": "array", "items": {"type": "string"}}),
        (
            {"additionalProperties": False},
            {"type": "object", "properties": {}, "additionalProperties": False},
        ),
        ({"type": "object", "properties": None}, {"type": "object"}),
        ({"type": "array", "items": None}, {"type": "array"}),
        (
            {"anyOf": [{"type": "null"}, {"type": "integer"}]},
            {"anyOf": [{"type": "null"}, {"type": "integer"}]},
        ),
    ],
)
def test_native_normalization_cases(raw, expected):
    assert normalized_input_schema(raw) == expected


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "null"},
        {"type": ["null"]},
        {"type": "object", "required": "x"},
        {"type": "string", "enum": "x"},
        {"type": "array", "minItems": True},
        {"type": "array", "minItems": -1},
        {"type": "array", "minItems": 2**64},
        {"type": "object", "properties": {"x": None}},
        {"type": "unknown", "$ref": "#/external"},
        {"type": "object", "description": "x" * 6000, "required": False},
    ],
)
def test_invalid_typed_schema_is_not_rescued_by_compaction(schema):
    with pytest.raises(ValueError):
        normalized_input_schema(schema)


def test_reachable_root_definitions_cycles_encoded_names_and_instance_values():
    raw = {
        "properties": {"x": {"$ref": "#/%24defs/A~1B/properties/x"}},
        "$defs": {
            "A/B": {"properties": {"next": {"$ref": "#/definitions/C"}}},
            "unused": {"type": "integer"},
        },
        "definitions": {"C": {"$ref": "#/$defs/A~1B"}},
        "enum": [{"$ref": "#/$defs/unused", "description": "instance"}],
    }
    actual = normalized_input_schema(raw)
    assert set(actual["$defs"]) == {"A/B"} and set(actual["definitions"]) == {"C"}
    assert actual["enum"] == raw["enum"]
    assert "type" not in actual["properties"]["x"]
    # Native reachability inside an already reached definition scans all Value
    # children, including instance data; root traversal is deliberately narrower.
    raw["$defs"]["A/B"]["enum"] = [{"$ref": "#/$defs/unused"}]
    assert set(normalized_input_schema(raw)["$defs"]) == {"A/B", "unused"}


def test_description_compaction_counts_projected_fields_and_preserves_enum_data():
    raw = {"type": "string", "description": "x" * 5100, "enum": [{"description": "instance"}]}
    assert normalized_input_schema(raw) == {"type": "string", "enum": raw["enum"]}
    assert normalized_input_schema(raw, compact=False) == raw
    raw = {
        "properties": {"x": {"type": "string", "description": "retained", "examples": ["x" * 9000]}}
    }
    assert normalized_input_schema(raw)["properties"]["x"] == {
        "type": "string",
        "description": "retained",
    }


def test_compaction_drops_local_refs_not_external_or_instance_refs():
    raw = {
        "properties": {
            "local": {"$ref": "#/$defs/L"},
            "external": {"$ref": "https://example.test/L"},
        },
        "$defs": {"L": {"type": "string", "enum": ["x" * 5100]}},
        "enum": [{"$ref": "#/$defs/L"}],
    }
    actual = normalized_input_schema(raw)
    assert "$defs" not in actual
    assert actual["properties"]["local"] == {}
    assert actual["properties"]["external"] == raw["properties"]["external"]
    assert actual["enum"] == raw["enum"]


def test_compaction_depth_and_composition_pass_order():
    raw = {
        "properties": {
            "a": {
                "properties": {
                    "b": {
                        "properties": {
                            "c": {"properties": {"d": {"type": "string", "enum": ["x" * 5100]}}}
                        }
                    }
                }
            }
        }
    }
    actual = normalized_input_schema(raw)
    assert actual["properties"]["a"]["properties"]["b"]["properties"]["c"] == {}
    raw = {"anyOf": [{"type": "string", "enum": ["x" * 5100]}]}
    assert normalized_input_schema(raw) == {}


@pytest.mark.parametrize("extra", [0, 1])
def test_agent_byte_boundary_uses_final_leaf_and_excludes_output_schema(extra):
    schema = {"type": "object", "properties": {"x": {"type": "string", "enum": [""]}}}
    base = {"name": "read", "description": "é", "strict": False, "parameters": schema}
    overhead = len(json.dumps(base, ensure_ascii=False, separators=(",", ":")).encode())
    schema["properties"]["x"]["enum"] = ["x" * (8000 - overhead + extra)]
    handler = tool(schema, agent=True, description="é")
    assert handler.spec.parameters == (
        {"type": "object", "properties": {}, "additionalProperties": True} if extra else schema
    )
    longer = handler.with_model_name(MCPToolName("docs", "read", "mcp__docs", "read_longer"))
    assert longer.spec.parameters == {
        "type": "object",
        "properties": {},
        "additionalProperties": True,
    }
    assert (
        longer.with_model_name(MCPToolName("docs", "read", "mcp__docs", "r")).spec.parameters
        == schema
    )


@pytest.mark.parametrize("extra", [0, 1])
def test_compaction_5000_boundary_uses_utf8_bytes(extra):
    raw = {"type": "string", "description": ""}
    overhead = len(json.dumps(raw, separators=(",", ":")).encode())
    size = 5000 - overhead + extra
    raw["description"] = "é" * (size // 2) + "x" * (size % 2)
    assert normalized_input_schema(raw) == ({"type": "string"} if extra else raw)
