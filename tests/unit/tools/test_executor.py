import asyncio
from pathlib import Path

from corki.protocol.ids import new_tool_call_id
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
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
            ToolCall(new_tool_call_id(), "update_plan", {"plan": []}),
            ToolContext(tmp_path),
        )
        assert invalid.is_error
        assert "at least 1" in invalid.content

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
