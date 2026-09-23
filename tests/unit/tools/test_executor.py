import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from corki.protocol.ids import new_tool_call_id
from corki.protocol.tools import (
    ImageAttachment,
    TextContent,
    ToolCall,
    ToolResult,
    ToolSpec,
    ToolStateUpdate,
)
from corki.tools import ToolContext, ToolExecutor, ToolRegistry
from corki.tools.builtin import UpdatePlanTool


def test_executor_normalizes_unknown_tool_and_invalid_arguments(tmp_path: Path) -> None:
    async def scenario() -> None:
        registry = ToolRegistry()
        registry.register(UpdatePlanTool())
        executor = ToolExecutor(registry, output_char_budget=1_000)

        unknown = await executor.execute(
            ToolCall(new_tool_call_id(), "missing", {}), ToolContext(tmp_path)
        )
        assert unknown.is_error
        assert "unknown tool" in unknown.content

        invalid = await executor.execute(
            ToolCall(new_tool_call_id(), "update_plan", {"plan": "not an array"}),
            ToolContext(tmp_path),
        )
        assert invalid.is_error
        assert "array" in invalid.content

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "value,valid",
    [(None, True), (1, True), (3, True), (0, False), (True, False), ("1", False), ([], False)],
)
def test_nullable_schema_alternatives_are_validated_before_dispatch(tmp_path, value, valid):
    async def scenario():
        calls = []

        class Tool:
            spec = ToolSpec(
                "nullable",
                "fixture",
                {
                    "type": "object",
                    "properties": {
                        "value": {"anyOf": [{"type": "integer", "minimum": 1}, {"type": "null"}]}
                    },
                    "required": ["value"],
                },
            )

            async def execute(self, call, context):
                calls.append(call)
                return ToolResult(call.id, call.name, "ok")

        registry = ToolRegistry()
        registry.register(Tool())
        result = await ToolExecutor(registry, output_char_budget=1000).execute(
            ToolCall(new_tool_call_id(), "nullable", {"value": value}),
            ToolContext(tmp_path),
        )
        assert result.is_error is not valid
        assert len(calls) == int(valid)

    asyncio.run(scenario())


def test_executor_rejects_mismatched_or_non_result_tool_output(tmp_path: Path) -> None:
    class BrokenTool:
        def __init__(self, value: object) -> None:
            self.value = value

        @property
        def spec(self) -> ToolSpec:
            return ToolSpec("broken", "broken", {"type": "object"})

        async def execute(self, call: ToolCall, context: ToolContext):
            del call, context
            return self.value

    async def scenario(value: object) -> ToolResult:
        registry = ToolRegistry()
        registry.register(BrokenTool(value))
        return await ToolExecutor(registry, output_char_budget=1_000).execute(
            ToolCall(new_tool_call_id(), "broken", {}), ToolContext(tmp_path)
        )

    wrong_identity = ToolResult(new_tool_call_id(), "someone_else", "bad")
    identity_error = asyncio.run(scenario(wrong_identity))
    type_error = asyncio.run(scenario("not a ToolResult"))

    assert identity_error.is_error
    assert "different call" in identity_error.content
    assert type_error.is_error
    assert "instead of ToolResult" in type_error.content


@pytest.mark.parametrize("source", ["parser", "pre_tool"])
def test_invalid_unicode_execution_input_is_rejected_before_handler(tmp_path, source):
    calls = []

    class Tool:
        spec = ToolSpec("fixture", "fixture", {"type": "object"})

        def parse_call_arguments(self, call):
            return {"value": "\ud800"} if source == "parser" else call.arguments

        async def execute(self, call, context):
            calls.append(call)
            return ToolResult(call.id, call.name, "side effect")

    async def before(call, tool):
        del tool
        return replace(call, arguments={"value": "\ud800"})

    async def scenario():
        registry = ToolRegistry()
        registry.register(Tool())
        return await ToolExecutor(registry, output_char_budget=1000).execute(
            ToolCall(new_tool_call_id(), "fixture", {}),
            ToolContext(tmp_path, before_tool=before if source == "pre_tool" else None),
        )

    result = asyncio.run(scenario())
    assert result.is_error and not calls


def test_rewritten_freeform_input_is_validated_before_handler(tmp_path):
    calls = []

    class Tool:
        spec = ToolSpec("fixture", "fixture", {}, input_kind="freeform")

        async def execute(self, call, context):
            calls.append(call)
            return ToolResult(call.id, call.name, "side effect")

    async def before(call, tool):
        del tool
        return replace(call, raw_arguments="\ud800")

    async def scenario():
        registry = ToolRegistry()
        registry.register(Tool())
        return await ToolExecutor(registry, output_char_budget=1000).execute(
            ToolCall(
                new_tool_call_id(), "fixture", None, raw_arguments="safe", input_kind="freeform"
            ),
            ToolContext(tmp_path, before_tool=before),
        )

    result = asyncio.run(scenario())
    assert result.is_error and not calls


def test_executor_owns_ordered_content_snapshot_after_handler_returns(tmp_path: Path) -> None:
    handler_parts = [TextContent("first")]

    class Tool:
        spec = ToolSpec("ordered", "fixture", {"type": "object"})

        async def execute(self, call, context):
            return ToolResult(call.id, call.name, "first", content_items=handler_parts)

    async def scenario():
        registry = ToolRegistry()
        registry.register(Tool())
        return await ToolExecutor(registry, output_char_budget=1000).execute(
            ToolCall(new_tool_call_id(), "ordered", {}), ToolContext(tmp_path)
        )

    result = asyncio.run(scenario())
    assert result.content_items == (TextContent("first"),)
    handler_parts.append(TextContent("later"))
    assert result.content_items == (TextContent("first"),)


@pytest.mark.parametrize("invalid_arguments", [False, True])
def test_error_original_is_preserved_and_model_and_display_obey_budget(tmp_path, invalid_arguments):
    class Tool:
        spec = ToolSpec("broken", "fixture", {"type": "object"}, output_char_budget=80)

        async def execute(self, call, context):
            raise ValueError("huge failure " * 10000)

    async def scenario():
        registry = ToolRegistry()
        registry.register(Tool())
        call = ToolCall(
            new_tool_call_id(),
            "broken",
            None if invalid_arguments else {},
            parse_error="invalid JSON " * 10000 if invalid_arguments else None,
        )
        result = await ToolExecutor(registry, output_char_budget=100).execute(
            call, ToolContext(tmp_path)
        )
        assert result.is_error
        from corki.context.function_output import project_function_items
        from corki.protocol.ids import new_turn_id
        from corki.protocol.items import ToolResultItem
        from corki.protocol.truncation import TruncationPolicy

        assert result.content == (
            "invalid JSON arguments: " + "invalid JSON " * 10000
            if invalid_arguments
            else "ValueError: " + "huge failure " * 10000
        )
        (visible,) = project_function_items(
            (
                ToolResultItem(
                    result.call_id,
                    result.tool_name,
                    result.content,
                    new_turn_id(),
                    legacy_output_char_budget=result.legacy_output_char_budget,
                ),
            ),
            TruncationPolicy(),
        )
        assert len(visible.content) <= 80
        assert len(result.display_content) <= 80

    asyncio.run(scenario())


def test_normalized_result_does_not_share_mutable_plan_or_attachment_list(tmp_path):
    async def scenario():
        plan = [{"step": "verify", "status": "in_progress"}]
        attachments = [ImageAttachment("data:image/png;base64,aGVsbG8=", "original")]

        class Tool:
            spec = ToolSpec("fixture", "fixture", {"type": "object"})

            async def execute(self, call, context):
                return ToolResult(
                    call.id,
                    call.name,
                    "ok",
                    attachments=attachments,
                    state_update=ToolStateUpdate(plan=plan),
                )

        registry = ToolRegistry()
        registry.register(Tool())
        result = await ToolExecutor(registry, output_char_budget=100).execute(
            ToolCall(new_tool_call_id(), "fixture", {}), ToolContext(tmp_path)
        )
        assert not result.is_error
        plan[0]["status"] = "INVALID"
        attachments.clear()
        assert result.state_update.plan == ({"step": "verify", "status": "in_progress"},)
        assert len(result.attachments) == 1 and result.attachments[0].detail == "original"

    asyncio.run(scenario())


def test_broken_exception_string_cannot_escape_observation_boundary(tmp_path):
    class BrokenError(Exception):
        def __str__(self):
            raise ValueError("broken exception formatter")

    class Tool:
        spec = ToolSpec("fixture", "fixture", {"type": "object"})

        async def execute(self, call, context):
            raise BrokenError()

    async def scenario():
        registry = ToolRegistry()
        registry.register(Tool())
        result = await ToolExecutor(registry, output_char_budget=100).execute(
            ToolCall(new_tool_call_id(), "fixture", {}), ToolContext(tmp_path)
        )
        assert result.is_error and "exception message unavailable" in result.content

    asyncio.run(scenario())


def test_handler_cannot_mutate_the_durable_call_arguments(tmp_path):
    class Tool:
        spec = ToolSpec("fixture", "fixture", {"type": "object"})

        async def execute(self, call, context):
            call.arguments["paths"].append("not-requested")
            return ToolResult(call.id, call.name, "ok")

    async def scenario():
        registry = ToolRegistry()
        registry.register(Tool())
        call = ToolCall(new_tool_call_id(), "fixture", {"paths": ["requested"]})
        result = await ToolExecutor(registry, output_char_budget=100).execute(
            call, ToolContext(tmp_path)
        )
        assert not result.is_error
        assert call.arguments == {"paths": ["requested"]}

    asyncio.run(scenario())
