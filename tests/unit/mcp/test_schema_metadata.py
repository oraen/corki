"""MCP schema is prompt metadata, not a Responses field or result validator."""

import json

import pytest

from corki.code_mode.specs import exec_spec, nested_description
from corki.core.checkpoint import checkpoint_serializer
from corki.mcp.tool_definition import validated_tool_fields
from corki.mcp.tools import MCPTool
from corki.models.namespaces import group_tool_definitions
from corki.protocol.tools import (
    ToolExposure,
    ToolSpec,
    tool_spec_from_payload,
    tool_spec_to_payload,
)
from corki.tools import ToolRegistry


def tool(definition, **kwargs):
    return MCPTool("docs", {"name": "read", **definition}, object(), **kwargs)


@pytest.mark.parametrize("output", [None, {}, {"enum": ["ok", "error"]}, {"$ref": "#/$defs/T"}])
@pytest.mark.parametrize("agent", [False, True])
@pytest.mark.parametrize("exposure", [ToolExposure.DIRECT, ToolExposure.DEFERRED])
def test_result_envelope_preserves_raw_schema_without_type_inference(output, agent, exposure):
    spec = tool({"outputSchema": output}, agent_plugin=agent, exposure=exposure).spec
    assert spec.output_schema == {
        "type": "object",
        "properties": {
            "content": {"type": "array", "items": {"type": "object"}},
            "structuredContent": output or {},
            "isError": {"type": "boolean"},
            "_meta": {"type": "object"},
        },
        "required": ["content"],
        "additionalProperties": False,
    }
    assert tool_spec_from_payload(tool_spec_to_payload(spec)) == spec
    serializer = checkpoint_serializer()
    assert serializer.loads_typed(serializer.dumps_typed(spec)) == spec
    for native in (False, True):
        for discovered in (False, True):
            wire = group_tool_definitions((spec,), native_namespaces=native, discovered=discovered)
            assert "output_schema" not in json.dumps(wire)
            assert "structuredContent" not in json.dumps(wire)
    assert "output_schema" not in spec.as_chat_completion_tool()["function"]


def test_absent_output_schema_still_describes_call_tool_result_and_copies_server_data():
    assert tool({}).spec.output_schema["properties"]["structuredContent"] == {}
    definition = {"outputSchema": {"properties": {"rows": {"type": "array"}}}}
    handler = tool(definition)
    first = handler.spec
    definition["outputSchema"]["properties"].clear()
    first.output_schema["properties"].clear()
    assert handler.spec.output_schema["properties"]["structuredContent"] == {
        "properties": {"rows": {"type": "array"}}
    }


@pytest.mark.parametrize("output", [True, False, [], "object", 1, 1.5])
def test_malformed_output_schema_rejected_before_live_or_cache_publication(output):
    definition = {"name": "read", "outputSchema": output}
    with pytest.raises(ValueError, match="output schema"):
        validated_tool_fields(definition)
    with pytest.raises(ValueError, match="output schema"):
        tool(definition)


@pytest.mark.parametrize("exposure", [ToolExposure.DIRECT, ToolExposure.DEFERRED])
def test_nested_metadata_contains_parameters_but_deferred_exec_prompt_does_not(exposure):
    schema = {
        "type": "object",
        "properties": {"record_id": {"type": "string", "description": "lookup key"}},
        "required": ["record_id"],
    }
    handler = tool({"description": "Lookup", "inputSchema": schema}, exposure=exposure)
    description = nested_description(handler.spec)
    assert json.dumps(schema, ensure_ascii=False) in description
    assert "structuredContent" in description
    registry = ToolRegistry()
    registry.register(handler)
    prompt = exec_spec(registry).description
    assert ("record_id" in prompt) == (exposure == ToolExposure.DIRECT)
    assert ("output_schema" in prompt) == (exposure == ToolExposure.DIRECT)


@pytest.mark.parametrize("kind", ["json", "freeform"])
def test_non_mcp_nested_tools_keep_their_input_contract_without_inventing_outputs(kind):
    spec = ToolSpec("extension", "Extension", {"type": "object"}, input_kind=kind)
    description = nested_description(spec)
    expected = {"type": "string"} if kind == "freeform" else spec.parameters
    assert description == "Extension\nInput schema: " + json.dumps(expected)
