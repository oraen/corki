"""Personality updates preserve the session base and append only typed changes."""

import asyncio
from dataclasses import replace

import pytest
from test_thread_settings_update import Model, make_runtime, settings

from corki.config.model_context import parse_model_contexts
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_turn_id
from corki.protocol.items import ContextItem
from corki.protocol.messages import Message, MessageRole


@pytest.mark.parametrize("custom_base", [None, "HOST BASE"])
@pytest.mark.parametrize("legacy_fragment", [False, True])
def test_personality_updates_are_persisted_and_not_repeated(tmp_path, custom_base, legacy_fragment):
    async def scenario():
        configured = replace(
            settings(tmp_path),
            personality="friendly",
            base_instructions=custom_base,
            model_contexts=parse_model_contexts(
                {
                    "large": {
                        "context_window": 200_000,
                        "model_messages": {
                            "instructions_template": "BASE {{ personality }}",
                            "instructions_variables": {
                                "personality_default": "D",
                                "personality_friendly": "F",
                                "personality_pragmatic": "P",
                            },
                        },
                    }
                }
            ),
        )
        model = Model()
        runtime = make_runtime(tmp_path, model, configured=configured)
        try:
            assert isinstance([e async for e in runtime.stream("first")][-1], TurnCompleted)
            original = await runtime._repository.load_items(runtime.thread_id)
            initial = [i for i in original if isinstance(i, ContextItem) and i.key == "personality"]
            assert len(initial) == 1
            assert bool(initial[0].content) is (custom_base is not None)
            selected = await runtime.update_thread_settings(personality="pragmatic")
            assert selected.personality == "pragmatic"
            persisted = await runtime._repository.load_thread_model_settings(runtime.thread_id)
            assert persisted.personality == "pragmatic"
            for text in ("second", "unchanged"):
                if legacy_fragment:
                    await runtime._repository.save_messages(
                        runtime.thread_id,
                        (
                            Message.create(
                                role=MessageRole.DEVELOPER,
                                content="<personality_spec>Legacy style</personality_spec>",
                                turn_id=new_turn_id(),
                            ),
                        ),
                    )
                assert isinstance([e async for e in runtime.stream(text)][-1], TurnCompleted)
            history = await runtime._repository.load_items(runtime.thread_id)
            changes = [
                i
                for i in history[len(original) :]
                if isinstance(i, ContextItem) and i.key == "personality"
            ]
            assert len(changes) == 1 and "<personality_spec>" in changes[0].content
            assert "P" in changes[0].content and changes[0].separate_message
            assert history[: len(original)] == original
            assert all(r.instructions == (custom_base or "BASE F") for r in model.requests)
            # An unrelated sparse update must preserve the published personality.
            assert (
                await runtime.update_thread_settings(reasoning_effort="high")
            ).personality == "pragmatic"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
