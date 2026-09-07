import asyncio
import gc
from dataclasses import replace
from weakref import ref

import pytest

from corki.core.step_tools import StepToolState
from corki.protocol.ids import ToolCallId
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools import ToolContext, ToolExecutor, ToolRegistry


class Tool:
    def __init__(self, spec, content="old"):
        self.spec, self.content, self.calls = spec, content, 0

    async def execute(self, call, context):
        self.calls += 1
        return ToolResult(call.id, call.name, self.content)


def test_snapshot_owns_handlers_until_released_and_exports_isolated_schemas():
    registry = ToolRegistry()
    owner = registry.create_owner()
    tool = Tool(ToolSpec("lookup", "lookup", {"properties": {"value": {"type": "string"}}}))
    weak = ref(tool)
    registry.replace_owned(owner, (tool,))
    registry.seal()
    snapshot = registry.snapshot()
    state = StepToolState()
    key = state.bind(snapshot)
    exported = snapshot.spec("lookup")
    exported.parameters["properties"]["value"]["type"] = "integer"
    assert snapshot.spec("lookup") == tool.spec
    registry.replace_owned(owner, ())
    del tool
    gc.collect()
    assert weak() is not None and state.resolve(key, registry) is snapshot
    state.bind(registry.snapshot())
    assert weak() is not None, "running caller must independently retain its old snapshot"
    del snapshot
    gc.collect()
    assert weak() is None


def test_executor_can_use_exact_old_handler_without_rebinding_by_name(tmp_path):
    async def scenario():
        spec = ToolSpec("lookup", "lookup", {})
        old, new = Tool(spec), Tool(spec, "new")
        registry = ToolRegistry()
        owner = registry.create_owner()
        registry.replace_owned(owner, (old,))
        snapshot = registry.snapshot()
        registry.replace_owned(owner, (new,))
        executor = ToolExecutor(registry, output_char_budget=1000)
        call = ToolCall(ToolCallId("call"), "lookup", {})
        assert (
            await executor.execute(call, ToolContext(tmp_path), spec=spec, snapshot=snapshot)
        ).content == "old"
        assert (await executor.execute(call, ToolContext(tmp_path), spec=spec)).content == "new"
        assert old.calls == new.calls == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("saved_key", [None, "persisted-binding-id"])
def test_cold_or_legacy_binding_still_rejects_changed_checkpoint_definition(tmp_path, saved_key):
    async def scenario():
        original = ToolSpec("lookup", "old definition", {})
        tool = Tool(replace(original, description="new definition"))
        registry = ToolRegistry()
        registry.register(tool)
        snapshot = StepToolState().resolve(saved_key, registry)
        result = await ToolExecutor(registry, output_char_budget=1000).execute(
            ToolCall(ToolCallId("cold"), "lookup", {}),
            ToolContext(tmp_path),
            spec=original,
            snapshot=snapshot,
        )
        assert result.is_error and "definition changed" in result.content and tool.calls == 0

    asyncio.run(scenario())


def test_empty_snapshot_does_not_fall_back_to_newly_registered_tool(tmp_path):
    async def scenario():
        registry = ToolRegistry()
        empty = registry.snapshot()
        tool = Tool(ToolSpec("lookup", "lookup", {}))
        registry.register(tool)
        result = await ToolExecutor(registry, output_char_budget=1000).execute(
            ToolCall(ToolCallId("s"), "lookup", {}),
            ToolContext(tmp_path),
            snapshot=empty,
        )
        assert result.is_error and "unknown tool" in result.content and tool.calls == 0

    asyncio.run(scenario())
