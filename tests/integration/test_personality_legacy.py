"""Old standalone developer fragments retain their identity on cold startup."""

import asyncio
from dataclasses import replace

import pytest
from test_thread_settings_update import Model, make_runtime, settings

from corki.config.model_context import parse_model_contexts
from corki.context.model_transition import model_snapshot
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_turn_id
from corki.protocol.items import ContextItem, ContextRole
from corki.protocol.messages import Message, MessageRole


@pytest.mark.parametrize("previous_model", [None, "large", "other"])
def test_legacy_personality_uses_only_known_model_reference(tmp_path, previous_model):
    async def scenario():
        configured = replace(
            settings(tmp_path),
            personality="friendly",
            base_instructions="HOST",
            model_contexts=parse_model_contexts(
                {
                    "large": {
                        "context_window": 200_000,
                        "model_messages": {
                            "instructions_template": "BASE {{ personality }}",
                            "instructions_variables": {"personality_friendly": "CURRENT"},
                        },
                    }
                }
            ),
        )
        source = make_runtime(tmp_path, Model(), configured=configured)
        try:
            await source._ensure_ready()
            thread = source.thread_id
            if previous_model is not None:
                await source._repository.append_items(
                    thread,
                    (model_snapshot(ModelContextInfo(previous_model, 200_000), new_turn_id()),),
                )
            await source._repository.save_messages(
                thread,
                (
                    Message.create(
                        role=MessageRole.DEVELOPER,
                        content="<personality_spec>OLD</personality_spec>",
                        turn_id=new_turn_id(),
                    ),
                ),
            )
            prefix = await source._repository.load_items(thread)
        finally:
            await source.aclose()

        for _ in range(2):
            model = Model()
            cold = make_runtime(tmp_path, model, configured=configured, thread=thread)
            try:
                assert isinstance([e async for e in cold.stream("next")][-1], TurnCompleted)
                stored = await cold._repository.load_items(thread)
                assert stored[: len(prefix)] == prefix
                updates = [
                    i
                    for i in stored[len(prefix) :]
                    if isinstance(i, ContextItem) and i.key in {"personality", "legacy.developer"}
                ]
                assert len(updates) == 1 and updates[0].key == "personality"
                assert updates[0].is_snapshot_only is (previous_model != "large")
                visible = [
                    i
                    for i in model.requests[0].items
                    if isinstance(i, ContextItem) and "personality_spec" in i.content
                ]
                assert len(visible) == (2 if previous_model == "large" else 1)
                assert visible[0].content == prefix[-1].content
                if previous_model == "large":
                    assert "CURRENT" in visible[1].content
            finally:
                await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "snapshot", ["{", "[]", "{}", '{"model":false}', '{"model":"large","personality":"invalid"}']
)
@pytest.mark.parametrize("visible", [False, True])
def test_invalid_old_personality_snapshot_falls_back_without_rewriting_history(
    tmp_path, snapshot, visible, caplog
):
    async def scenario():
        configured = replace(
            settings(tmp_path),
            personality="friendly",
            base_instructions="HOST",
            model_contexts=parse_model_contexts(
                {
                    "large": {
                        "context_window": 200_000,
                        "model_messages": {
                            "instructions_template": "BASE {{ personality }}",
                            "instructions_variables": {"personality_friendly": "CURRENT"},
                        },
                    }
                }
            ),
        )
        source = make_runtime(tmp_path, Model(), configured=configured)
        try:
            await source._ensure_ready()
            thread = source.thread_id
            await source._repository.append_items(
                thread,
                (
                    model_snapshot(ModelContextInfo("large", 200_000), new_turn_id()),
                    ContextItem(
                        "personality",
                        ContextRole.DEVELOPER,
                        "<personality_spec>OLD VISIBLE</personality_spec>" if visible else "",
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
                if isinstance(i, ContextItem) and i.key == "personality"
            ]
            assert len(updates) == 1 and "CURRENT" in updates[0].content
            assert len(model.requests) == 2
            for request in model.requests:
                old = [
                    i
                    for i in request.items
                    if isinstance(i, ContextItem) and "OLD VISIBLE" in i.content
                ]
                assert old == ([prefix[-1]] if visible else [])
            assert "Failed to restore personality snapshot" in caplog.text
        finally:
            await cold.aclose()

    asyncio.run(scenario())
