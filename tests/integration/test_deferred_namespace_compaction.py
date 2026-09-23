import asyncio
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.context.deferred_tools import KEY, decode_snapshot
from corki.context.history import active_history
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import ContextCompacted, TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolExposure, ToolResult, ToolSpec
from corki.tools import ToolRegistry


class Read:
    spec = ToolSpec(
        "vault::read",
        "vaultproof",
        {},
        exposure=ToolExposure.DEFERRED,
        namespace_description="old metadata",
    )

    async def execute(self, call, context):
        return ToolResult(call.id, call.name, "IMPORTANT FACT\n" + "x" * 16000)


@pytest.mark.parametrize("threshold, expected_compactions", [(4000, 1), (3000, 2)])
def test_automatic_compaction_reinjects_full_current_map_and_releases_loaded_tool(
    tmp_path, threshold, expected_compactions
):
    async def scenario():
        registry = ToolRegistry()
        owner = registry.create_owner()
        executions = []

        class TrackedRead(Read):
            async def execute(self, call, context):
                executions.append(self.spec.namespace_description)
                if len(executions) == 1:
                    return await super().execute(call, context)
                return ToolResult(call.id, call.name, "FRESH_READ_PROOF")

        registry.replace_owned(owner, (TrackedRead(),))
        requests = []
        summaries = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if (
                    isinstance(request.items[-1], UserMessageItem)
                    and "checkpoint compaction" in request.items[-1].content
                ):
                    summaries.append(request)
                    assert not request.tools
                    proof = "IMPORTANT FACT" if len(summaries) == 1 else "FRESH_READ_PROOF"
                    assert any(
                        isinstance(i, ToolResultItem) and proof in i.content for i in request.items
                    ), f"summary request {len(requests)} has no {proof}"
                    item = AssistantMessageItem(proof, turn, step)
                elif len(requests) == 1:
                    item = ToolCallItem(
                        ToolCall(new_tool_call_id(), "tool_search", {"query": "vaultproof"}),
                        turn,
                        step,
                    )
                elif len(requests) == 2:
                    changed = TrackedRead()
                    changed.spec = replace(Read.spec, namespace_description="new metadata")
                    registry.replace_owned(owner, (changed,))
                    item = ToolCallItem(ToolCall(new_tool_call_id(), "vault::read", {}), turn, step)
                elif len(requests) == 4:
                    rows = [i for i in request.items if isinstance(i, ContextItem) and i.key == KEY]
                    assert len(rows) == 1
                    assert rows[0].content.startswith(
                        "<tools>\nDeferred tool namespaces:\n- vault: new metadata"
                    )
                    assert decode_snapshot(rows[0].snapshot_state) == {"vault": "new metadata"}
                    assert all(s.name != "vault::read" for s in request.tools)
                    assert any(
                        isinstance(i, UserMessageItem) and i.content == "CURRENT INPUT"
                        for i in request.items
                    )
                    item = ToolCallItem(
                        ToolCall(new_tool_call_id(), "tool_search", {"query": "vaultproof"}),
                        turn,
                        step,
                    )
                elif len(requests) == 5:
                    loaded = [s for s in request.tools if s.name == "vault::read"]
                    assert len(loaded) == 1
                    assert loaded[0].namespace_description == "new metadata"
                    item = ToolCallItem(ToolCall(new_tool_call_id(), "vault::read", {}), turn, step)
                else:
                    assert any(
                        (
                            isinstance(i, ToolResultItem) and i.content == "FRESH_READ_PROOF"
                            if expected_compactions == 1
                            else isinstance(i, CompactionItem) and i.summary == "FRESH_READ_PROOF"
                        )
                        for i in request.items
                    ), f"ordinary request {len(requests)} has no fresh read result"
                    assert any(
                        isinstance(i, UserMessageItem) and i.content == "CURRENT INPUT"
                        for i in request.items
                    )
                    if expected_compactions == 2:
                        assert all(s.name != "vault::read" for s in request.tools)
                    item = AssistantMessageItem("done", turn, step)
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        runtime = await make_runtime(
            # Both thresholds compact the first large read. The lower one also
            # compacts the subsequent fresh search/read cycle.
            tmp_path,
            registry,
            Model(),
            context_window_tokens=8000,
            auto_compact_tokens=threshold,
        )
        try:
            events = [e async for e in runtime.stream("CURRENT INPUT")]
            assert isinstance(events[-1], TurnCompleted), (
                f"requests={len(requests)}, executions={executions}, terminal={events[-1]}"
            )
            assert sum(isinstance(e, ContextCompacted) for e in events) == expected_compactions
            assert len(summaries) == expected_compactions
            assert len(requests) == 5 + expected_compactions
            # The first call retains its admitted Step snapshot; the second uses
            # the changed catalog only after a fresh ordinary tool_search.
            assert executions == ["old metadata", "new metadata"]
            raw = await runtime._repository.load_items(runtime.thread_id)
            assert any(isinstance(i, ToolResultItem) and len(i.content) > 16000 for i in raw)
            maps = [
                decode_snapshot(i.snapshot_state)
                for i in raw
                if isinstance(i, ContextItem) and i.key == KEY
            ]
            assert {"vault": "old metadata"} in maps and {"vault": "new metadata"} in maps
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_token_budget_summary_rebuilds_namespace_snapshot_on_next_turn(tmp_path):
    async def scenario():
        registry = ToolRegistry()
        owner = registry.create_owner()
        registry.replace_owned(owner, (Read(),))
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await make_runtime(tmp_path, registry, Model(), token_budget_enabled=True)
        try:
            events = [e async for e in runtime.stream("initial")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            changed = Read()
            changed.spec = replace(Read.spec, namespace_description="new metadata")
            registry.replace_owned(owner, (changed,))
            events = [e async for e in runtime.compact()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 2 and not requests[-1].tools
            events = [e async for e in runtime.stream("continue with fresh context")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 3
            rows = [
                i
                for i in active_history(await runtime._repository.load_items(runtime.thread_id))
                if isinstance(i, ContextItem) and i.key == KEY
            ]
            assert len(rows) == 1 and decode_snapshot(rows[0].snapshot_state) == {
                "vault": "new metadata"
            }
            assert rows[0].content.startswith("<tools>\nDeferred tool namespaces:")
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


async def make_runtime(tmp_path, registry, model, **settings):
    return await LangGraphRuntime.acreate(
        settings=CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            deferred_tool_world_state=True,
            model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
            **settings,
        ),
        database_path=tmp_path / "history.db",
        home_path=tmp_path / "home",
        registry=registry,
        model=model,
    )
