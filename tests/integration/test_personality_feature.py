"""Feature-off uses catalog defaults, not explicit-none personality semantics."""

import asyncio
from dataclasses import replace

import pytest
from test_thread_settings_update import Model, make_runtime, settings

from corki.config.model_context import parse_model_contexts
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ContextItem


@pytest.mark.parametrize(
    "enabled, selected, expected",
    [
        (True, "friendly", "BASE\n# Personality\nF\n# Rules\nKEEP"),
        (True, "none", "BASE\n# Rules\nKEEP"),
        (False, "friendly", "BASE\n# Personality\nD\n# Rules\nKEEP"),
        (False, "none", "BASE\n# Personality\nD\n# Rules\nKEEP"),
    ],
)
def test_personality_feature_and_explicit_none_are_distinct(tmp_path, enabled, selected, expected):
    async def scenario():
        configured = replace(
            settings(tmp_path),
            personality=selected,
            personality_enabled=enabled,
            model_contexts=parse_model_contexts(
                {
                    "large": {
                        "context_window": 200_000,
                        "model_messages": {
                            "instructions_template": (
                                "BASE\n# Personality\n{{ personality }}\n# Rules\nKEEP"
                            ),
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
            events = [e async for e in runtime.stream("input")]
            assert isinstance(events[-1], TurnCompleted)
            assert model.requests[0].instructions == expected
            stored = await runtime._repository.load_items(runtime.thread_id)
            states = [i for i in stored if isinstance(i, ContextItem) and i.key == "personality"]
            assert len(states) == int(enabled)
            checkpoint = await runtime._checkpointer.aget_tuple(
                runtime._graph_config(events[-1].turn_id)
            )
            assert (
                checkpoint.checkpoint["channel_values"]["turn_model_settings"].personality_enabled
                is enabled
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
