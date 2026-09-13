"""Broken comparison metadata cannot replace trusted accepted-model identity."""

import asyncio
from dataclasses import replace

import pytest
from test_thread_settings_update import Model, make_runtime, settings

from corki.context.model_transition import model_snapshot
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_turn_id
from corki.protocol.items import CompactionItem, ContextItem, ContextRole


@pytest.mark.parametrize("snapshot", ["{", "[]", "{}", '{"model":false}'])
@pytest.mark.parametrize("visible", [False, True])
@pytest.mark.parametrize("previous", [None, "large", "small"])
def test_broken_old_model_snapshot_uses_accepted_identity(tmp_path, snapshot, visible, previous):
    async def scenario():
        configured = replace(
            settings(tmp_path),
            model_contexts=(ModelContextInfo("large", 200_000, base_instructions="CURRENT RULES"),),
        )
        source = make_runtime(tmp_path, Model(), configured=configured)
        try:
            await source._ensure_ready()
            thread = source.thread_id
            await source._repository.append_items(
                thread,
                (
                    *(
                        (model_snapshot(ModelContextInfo(previous, 200_000), new_turn_id()),)
                        if previous is not None
                        else ()
                    ),
                    ContextItem(
                        "model.instructions",
                        ContextRole.DEVELOPER,
                        "<model_switch>OLD RULES</model_switch>" if visible else "",
                        new_turn_id(),
                        snapshot_state=snapshot,
                    ),
                ),
            )
            prefix = await source._repository.load_items(thread)
        finally:
            await source.aclose()
        model = Model()
        cold = make_runtime(tmp_path, model, configured=configured, thread=thread)
        try:
            for _ in range(2):
                events = [e async for e in cold.stream("next")]
                assert isinstance(events[-1], TurnCompleted), events[-1]
            history = await cold._repository.load_items(thread)
            assert history[: len(prefix)] == prefix
            updates = [
                i
                for i in history[len(prefix) :]
                if isinstance(i, ContextItem) and i.key == "model.instructions"
            ]
            assert len(updates) == 1
            assert bool(updates[0].content) is (previous == "small")
            if previous == "small":
                assert "CURRENT RULES" in updates[0].content
            assert len(model.requests) == 2
            for request in model.requests:
                old = [
                    i
                    for i in request.items
                    if isinstance(i, ContextItem) and "OLD RULES" in i.content
                ]
                assert old == ([prefix[-1]] if visible else [])
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("compacted", [False, True])
@pytest.mark.parametrize("provenance", ["model", "custom"])
def test_unknown_model_snapshot_fallback_respects_base_provenance(tmp_path, compacted, provenance):
    async def scenario():
        configured = replace(
            settings(tmp_path),
            model_contexts=(
                ModelContextInfo("large", 200_000, base_instructions="CURRENT RULES"),
                ModelContextInfo("small", 200_000, base_instructions="FROZEN BASE"),
            ),
        )
        source = make_runtime(
            tmp_path,
            Model(),
            configured=replace(
                configured,
                model="small",
                base_instructions="FROZEN BASE" if provenance == "custom" else None,
            ),
        )
        try:
            await source._ensure_ready()
            thread = source.thread_id
            old = ContextItem(
                "model.instructions",
                ContextRole.DEVELOPER,
                "<model_switch>OLD RULES</model_switch>",
                new_turn_id(),
                snapshot_state="{",
            )
            # Persisted history fixture: replay a prior compaction, not a new
            # summary request. The broken row still exists in the raw journal.
            items = (
                (old, CompactionItem("OLD SUMMARY", old.id, new_turn_id())) if compacted else (old,)
            )
            await source._repository.append_items(thread, items)
            prefix = await source._repository.load_items(thread)
        finally:
            await source.aclose()
        model = Model()
        cold = make_runtime(tmp_path, model, configured=configured, thread=thread)
        try:
            for _ in range(2):
                events = [e async for e in cold.stream("next")]
                assert isinstance(events[-1], TurnCompleted), events[-1]
            history = await cold._repository.load_items(thread)
            assert history[: len(prefix)] == prefix
            updates = [
                i
                for i in history[len(prefix) :]
                if isinstance(i, ContextItem) and i.key == "model.instructions"
            ]
            assert len(updates) == 1
            assert bool(updates[0].content) is (provenance == "model")
            assert len(model.requests) == 2
            for request in model.requests:
                assert request.instructions == "FROZEN BASE"
                assert any(
                    isinstance(i, ContextItem) and "OLD RULES" in i.content for i in request.items
                ) is (not compacted)
                assert ("OLD SUMMARY" in str(request.items)) is compacted
        finally:
            await cold.aclose()

    asyncio.run(scenario())
