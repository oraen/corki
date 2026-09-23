"""Over-nested comparison JSON is unknown metadata, not execution authority."""

import asyncio
from dataclasses import replace

import pytest
from test_deferred_namespace_context import Read
from test_thread_settings_update import Model, make_runtime, settings

from corki.context import deferred_tools, environment, model_transition, permissions
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_turn_id
from corki.protocol.items import ContextItem, ContextRole
from corki.tools import ToolRegistry


@pytest.mark.parametrize("section", [environment, deferred_tools])
@pytest.mark.parametrize("visible", [False, True])
@pytest.mark.parametrize("depth", [2000, 16000])
def test_deep_old_comparison_snapshot_does_not_abort_cold_turn(tmp_path, section, visible, depth):
    async def scenario():
        calls = []
        configured = replace(
            settings(tmp_path), include_environment_context=True, deferred_tool_world_state=True
        )

        async def create(model, thread=None):
            registry = ToolRegistry()
            registry.register(Read(calls))
            return await make_runtime(
                tmp_path, model, configured=configured, registry=registry, thread=thread
            )

        source = await create(Model())
        try:
            await source._ensure_ready()
            thread = source.thread_id
            old = ContextItem(
                section.KEY,
                ContextRole.USER if section is environment else ContextRole.DEVELOPER,
                "OLD VISIBLE CONTEXT" if visible else "",
                new_turn_id(),
                snapshot_state="[" * depth + "0" + "]" * depth,
            )
            await source._repository.append_items(thread, (old,))
            prefix = await source._repository.load_items(thread)
        finally:
            await source.aclose()
        model = Model()
        cold = await create(model, thread)
        try:
            for _ in range(2):
                events = [e async for e in cold.stream("next")]
                assert isinstance(events[-1], TurnCompleted), events[-1]
            history = await cold._repository.load_items(thread)
            assert history[: len(prefix)] == prefix
            updates = [
                i
                for i in history[len(prefix) :]
                if isinstance(i, ContextItem) and i.key == section.KEY
            ]
            assert len(updates) == 1
            assert section.decode_snapshot(updates[0].snapshot_state) is not None
            assert len(model.requests) == 2 and calls == []
            for request in model.requests:
                assert [
                    i
                    for i in request.items
                    if isinstance(i, ContextItem) and i.content == "OLD VISIBLE CONTEXT"
                ] == ([old] if visible else [])
                assert all(s.name != Read.spec.name for s in request.tools)
        finally:
            await cold.aclose()

    asyncio.run(scenario())


def test_deep_old_model_transition_marker_does_not_abort_cold_turn(tmp_path):
    async def scenario():
        configured = settings(tmp_path)
        source = await make_runtime(tmp_path, Model(), configured=configured)
        try:
            await source._ensure_ready()
            thread = source.thread_id
            old = ContextItem(
                model_transition.KEY,
                ContextRole.DEVELOPER,
                "",
                new_turn_id(),
                snapshot_state="[" * 16000 + "0" + "]" * 16000,
            )
            await source._repository.append_items(thread, (old,))
            prefix = await source._repository.load_items(thread)
        finally:
            await source.aclose()
        model = Model()
        cold = await make_runtime(tmp_path, model, configured=configured, thread=thread)
        try:
            events = [e async for e in cold.stream("next")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            history = await cold._repository.load_items(thread)
            assert history[: len(prefix)] == prefix
            assert len(model.requests) == 1
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("full", [False, True])
def test_deep_old_permission_snapshot_does_not_abort_cold_turn(tmp_path, full):
    async def scenario():
        configured = replace(settings(tmp_path), include_permissions_instructions=full)
        source = await make_runtime(tmp_path, Model(), configured=configured)
        key = permissions.KEY if full else permissions.COMPACT_KEY
        try:
            await source._ensure_ready()
            thread = source.thread_id
            old = ContextItem(
                key,
                ContextRole.DEVELOPER,
                "OLD VISIBLE PERMISSION CONTEXT" if full else "",
                new_turn_id(),
                snapshot_state="[" * 16000 + "0" + "]" * 16000,
            )
            await source._repository.append_items(thread, (old,))
            prefix = await source._repository.load_items(thread)
        finally:
            await source.aclose()
        model = Model()
        cold = await make_runtime(tmp_path, model, configured=configured, thread=thread)
        try:
            for _ in range(2):
                events = [e async for e in cold.stream("next")]
                assert isinstance(events[-1], TurnCompleted), events[-1]
            history = await cold._repository.load_items(thread)
            assert history[: len(prefix)] == prefix
            updates = [
                item
                for item in history[len(prefix) :]
                if isinstance(item, ContextItem) and item.key == key
            ]
            assert len(updates) == 1
            assert permissions._decode(updates[0]) is not None
            assert len(model.requests) == 2
            if full:
                assert [
                    sum(
                        isinstance(item, ContextItem) and item.content == old.content
                        for item in request.items
                    )
                    for request in model.requests
                ] == [1, 1]
        finally:
            await cold.aclose()

    asyncio.run(scenario())
