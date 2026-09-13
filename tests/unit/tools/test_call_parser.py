"""Handler-owned parsing cannot alter durable call identity or weaken other tools."""

import asyncio
from dataclasses import replace

import pytest

from corki.core.checkpoint import checkpoint_serializer
from corki.protocol.ids import new_tool_call_id
from corki.protocol.tools import (
    ToolCall,
    ToolResult,
    ToolSpec,
    tool_spec_from_payload,
    tool_spec_to_payload,
)
from corki.tools import ToolExecutor, ToolRegistry
from corki.tools.base import ToolContext


@pytest.mark.parametrize("owns_parser", [False, True])
def test_parser_opt_in_is_local_and_keeps_original_call(tmp_path, owns_parser):
    async def scenario():
        seen = []

        class Tool:
            spec = ToolSpec(
                "parse",
                "fixture",
                {
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                    "required": ["value"],
                },
            )

            async def execute(self, call, context):
                seen.append(call)
                return ToolResult(call.id, call.name, "ok")

        class Parsed(Tool):
            def parse_call_arguments(self, call):
                assert call.parse_error is not None
                return {"value": 7}

        registry = ToolRegistry()
        registry.register(Parsed() if owns_parser else Tool())
        executor = ToolExecutor(registry, output_char_budget=1000)
        call = ToolCall(
            new_tool_call_id(), "parse", None, raw_arguments=" custom ", parse_error="not JSON"
        )
        result = await executor.execute(call, ToolContext(cwd=tmp_path))
        assert result.is_error is not owns_parser
        assert (
            call.arguments is None
            and call.parse_error == "not JSON"
            and call.raw_arguments == " custom "
        )
        if owns_parser:
            assert seen[0].id == call.id and seen[0].name == call.name
            assert seen[0].raw_arguments == call.raw_arguments and seen[0].arguments == {"value": 7}
        else:
            assert not seen

    asyncio.run(scenario())


def test_output_schema_roundtrips_but_never_becomes_a_wire_tool_field():
    original = ToolSpec("memories::read", "Read", {"type": "object"})
    legacy = tool_spec_to_payload(original)
    assert "output_schema" not in legacy
    assert tool_spec_from_payload(legacy) == original
    schema = {"type": "object", "properties": {"content": {"type": "string"}}}
    typed = replace(original, output_schema=schema)
    schema["properties"]["content"]["type"] = "number"
    assert typed.output_schema["properties"]["content"]["type"] == "string"
    assert tool_spec_from_payload(tool_spec_to_payload(typed)) == typed
    serializer = checkpoint_serializer()
    assert serializer.loads_typed(serializer.dumps_typed(typed)) == typed
    assert "output_schema" not in typed.as_response_tool(native_namespaces=True)
    assert "output_schema" not in typed.as_chat_completion_tool()["function"]


@pytest.mark.parametrize("number", [-(2**100), 2**64, 2**128, 2**512])
def test_exact_large_argument_mapping_survives_checkpoint_without_pickle(number):
    arguments = {"nested": [{"number": number}], "ordinary": 3}
    call = ToolCall(new_tool_call_id(), "probe", arguments)
    serializer = checkpoint_serializer()
    encoded = serializer.dumps_typed(call)
    assert encoded[0] == "msgpack"
    restored = serializer.loads_typed(encoded)
    assert restored == call and restored.arguments == arguments
    assert type(restored.arguments["nested"][0]["number"]) is int
    assert restored.raw_arguments == ""


def test_pre_output_schema_msgpack_definition_uses_new_field_default():
    # Serialized by immutable batch149, before ToolSpec gained output_schema.
    payload = bytes.fromhex(
        "c801740293b4636f726b692e70726f746f636f6c2e746f6f6c73a8546f6f6c537065638ca46e616d"
        "65a66c6567616379ab6465736372697074696f6ea64c6567616379aa706172616d657465727382a4"
        "74797065a66f626a656374aa70726f7065727469657380a86578706f73757265c72a0093b4636f72"
        "6b692e70726f746f636f6c2e746f6f6c73ac546f6f6c4578706f73757265a6646972656374ab636f"
        "6e63757272656e6379c7300093b4636f726b692e70726f746f636f6c2e746f6f6c73af546f6f6c43"
        "6f6e63757272656e6379a96578636c7573697665b26f75747075745f636861725f627564676574c0"
        "ab7365617263685f74657874c0a6736f75726365c0aa696e7075745f6b696e64c7290093b4636f72"
        "6b692e70726f746f636f6c2e746f6f6c73ad546f6f6c496e7075744b696e64a46a736f6eaf667265"
        "65666f726d5f666f726d6174c0b2736f757263655f6465736372697074696f6ec0b56e616d657370"
        "6163655f6465736372697074696f6ec0"
    )
    restored = checkpoint_serializer().loads_typed(("msgpack", payload))
    assert restored == ToolSpec("legacy", "Legacy", {"type": "object", "properties": {}})
    assert restored.output_schema is None
