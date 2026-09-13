"""Deferred context follows source order across discovery, refresh and compaction."""

import asyncio

import pytest
from test_code_mode import request_call

from corki.config import CorkiSettings
from corki.context.deferred_tools import KEY, decode_snapshot
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import ContextCompacted, TurnCompleted
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ContextItem,
    ToolResultItem,
    new_step_id,
)
from corki.protocol.tools import ToolExposure, ToolResult, ToolSpec
from corki.tools import ToolRegistry, ToolSource


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("transition", ["refresh", "cold", "compact"])
def test_context_uses_first_source_description_and_reordered_next_generation(
    tmp_path, native, transition
):
    async def scenario():
        requests, executed = [], []
        state = {"description": "First namespace.", "leaf": "z", "phase": 0, "compacting": False}

        class Tool:
            def __init__(self, name, description):
                self.spec = ToolSpec(
                    f"shared::{name}",
                    "orderproof",
                    {},
                    exposure=ToolExposure.DEFERRED,
                    namespace_description=description,
                )

            async def execute(self, call, context):
                executed.append(call.name)
                return ToolResult(call.id, call.name, "EXECUTED_" + call.name)

        tools = (Tool("z", "First namespace."), Tool("a", "Second namespace."))

        class Model:
            async def stream(self, request):
                requests.append(request)
                if state["compacting"]:
                    item = (
                        CompactionItem(
                            "",
                            None,
                            request.compaction_turn_id,
                            remote_payload_json='{"type":"compaction","encrypted_content":"OPAQUE"}',
                        )
                        if request.compaction_turn_id is not None
                        else AssistantMessageItem(
                            "Retain orderproof results.", request.items[-1].turn_id, new_step_id()
                        )
                    )
                    yield ModelCompleted((item,))
                    return
                hints = [
                    i
                    for i in (*request.context_items, *request.items)
                    if isinstance(i, ContextItem) and i.key == KEY
                ]
                assert hints and decode_snapshot(hints[-1].snapshot_state) == {
                    "shared": state["description"]
                }
                assert f"- shared: {state['description']}" in hints[-1].content
                state["phase"] += 1
                if state["phase"] == 1:
                    yield request_call(request, "tool_search", {"query": "orderproof"})
                elif state["phase"] == 2:
                    found = [
                        i
                        for i in request.items
                        if isinstance(i, ToolResultItem) and i.tool_name == "tool_search"
                    ][-1]
                    assert {s.name for s in found.discovered_tools} == {"shared::a", "shared::z"}
                    yield request_call(request, "shared::" + state["leaf"], {})
                else:
                    assert any(
                        isinstance(i, ToolResultItem)
                        and "EXECUTED_shared::" + state["leaf"] in i.content
                        for i in request.items
                    )
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        def create(thread=None):
            registry = ToolRegistry()
            owner = registry.create_owner(source=ToolSource.DYNAMIC)
            registry.replace_owned(owner, tools if thread is None else tuple(reversed(tools)))
            runtime = LangGraphRuntime.create(
                settings=CorkiSettings(
                    tmp_path,
                    skills_enabled=False,
                    deferred_tool_world_state=True,
                    api_mode="responses",
                    tool_search_mode="native" if native else "compatible",
                    model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
                ),
                registry=registry,
                model=Model(),
                database_path=tmp_path / "s.db",
                thread_id=thread,
            )
            return runtime, registry, owner

        runtime, registry, owner = create()
        try:
            first = [e async for e in runtime.stream("read first source")]
            assert isinstance(first[-1], TurnCompleted), first[-1]
            before = await runtime._repository.load_items(runtime.thread_id)
            state.update(description="Second namespace.", leaf="a", phase=0)
            if transition == "cold":
                thread = runtime.thread_id
                await runtime.aclose()
                runtime, registry, owner = create(thread)
            else:
                registry.replace_owned(owner, tuple(reversed(tools)))
            if transition == "compact":
                state["compacting"] = True
                compacted = [e async for e in runtime.compact()]
                assert isinstance(compacted[-1], TurnCompleted), compacted[-1]
                assert any(isinstance(e, ContextCompacted) for e in compacted)
                state["compacting"] = False
            second = [e async for e in runtime.stream("read reordered source")]
            assert isinstance(second[-1], TurnCompleted), second[-1]
            assert executed == ["shared::z", "shared::a"]
            after = await runtime._repository.load_items(runtime.thread_id)
            assert all(item in after for item in before)
            hints = [i for i in after if isinstance(i, ContextItem) and i.key == KEY]
            assert decode_snapshot(hints[0].snapshot_state) == {"shared": "First namespace."}
            assert decode_snapshot(hints[-1].snapshot_state) == {"shared": "Second namespace."}
            if transition != "compact":
                assert "Added deferred tool namespaces:" in hints[-1].content
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
