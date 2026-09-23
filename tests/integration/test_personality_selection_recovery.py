"""An admitted personality selection is not replaced by new host defaults."""

import asyncio
from dataclasses import replace

import pytest
from test_thread_settings_update import Model, make_runtime, settings

from corki.config.model_context import parse_model_contexts
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_turn_id
from corki.protocol.items import UserMessageItem
from corki.sessions import TurnRecord, TurnStatus


@pytest.mark.parametrize(
    "personality, expected",
    [(None, "DEFAULT"), ("none", ""), ("friendly", "FRIENDLY"), ("pragmatic", "PRAGMATIC")],
)
@pytest.mark.parametrize("enabled", [False, True])
def test_personality_selection_is_frozen_before_cold_resume(
    tmp_path, personality, expected, enabled
):
    async def scenario():
        class Sink:
            async def emit(self, event):
                pass

        configured = replace(
            settings(tmp_path),
            personality=personality,
            personality_enabled=enabled,
            model_contexts=parse_model_contexts(
                {
                    "large": {
                        "context_window": 200_000,
                        "model_messages": {
                            "instructions_template": "BASE {{ personality }}",
                            "instructions_variables": {
                                "personality_default": "DEFAULT",
                                "personality_friendly": "FRIENDLY",
                                "personality_pragmatic": "PRAGMATIC",
                            },
                        },
                    }
                }
            ),
        )
        source = await make_runtime(tmp_path, Model(), configured=configured)
        try:
            await source._ensure_ready()
            turn = new_turn_id()
            user = UserMessageItem("input", turn)
            state = _initial_state(source.thread_id, turn, source._settings, user)
            await source._repository.save_turn(
                TurnRecord(
                    turn,
                    source.thread_id,
                    TurnStatus.RUNNING,
                    user.content,
                    model_settings=state["turn_model_settings"],
                )
            )
            await source._compiled.ainvoke(
                state,
                config=source._graph_config(turn),
                context=GraphRunContext(events=Sink()),
                interrupt_before=["call_model"],
            )
            thread = source.thread_id
        finally:
            await source.aclose()

        model = Model()
        cold = await make_runtime(
            tmp_path,
            model,
            configured=replace(configured, personality="none", personality_enabled=not enabled),
            thread=thread,
        )
        try:
            pending = await cold._repository.latest_running_turn(thread)
            assert pending.model_settings.personality == personality
            assert pending.model_settings.personality_enabled is enabled
            assert isinstance([e async for e in cold.resume_pending()][-1], TurnCompleted)
            assert len(model.requests) == 1
            assert model.requests[0].instructions == f"BASE {expected if enabled else 'DEFAULT'}"
            checkpoint = await cold._checkpointer.aget_tuple(cold._graph_config(turn))
            assert (
                checkpoint.checkpoint["channel_values"]["turn_model_settings"].personality
                == personality
            )
            assert [e async for e in cold.resume_pending()] == []
        finally:
            await cold.aclose()

    asyncio.run(scenario())
