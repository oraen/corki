"""Local instruction templates enter the real Runtime without a catalog service."""

import asyncio
from dataclasses import replace

import pytest
from test_thread_settings_update import Model, make_runtime, settings

from corki.config.model_context import parse_model_contexts
from corki.protocol.events import TurnCompleted
from corki.protocol.settings import ModelSettingsSnapshot


@pytest.mark.parametrize(
    "messages, expected",
    [
        ({"instructions_template": "BASE {{ personality }}"}, "BASE {{ personality }}"),
        (
            {"instructions_template": "BASE {{ personality }}", "instructions_variables": {}},
            "BASE ",
        ),
        (
            {
                "instructions_template": "BASE {{ personality }}",
                "instructions_variables": {"personality_default": "DEFAULT"},
            },
            "BASE DEFAULT",
        ),
        ({"instructions_template": None, "instructions_variables": {}}, ""),
    ],
)
def test_local_template_enters_runtime_and_round_trips_snapshot(tmp_path, messages, expected):
    async def scenario():
        configured = replace(
            settings(tmp_path),
            model_contexts=parse_model_contexts(
                {"large": {"context_window": 200_000, "model_messages": messages}}
            ),
        )
        model = Model()
        runtime = make_runtime(tmp_path, model, configured=configured)
        try:
            events = [e async for e in runtime.stream("input")]
            assert isinstance(events[-1], TurnCompleted)
            assert model.requests[0].instructions == expected
            snapshot = runtime._thread_settings.snapshot
            assert ModelSettingsSnapshot.from_payload(snapshot.to_payload()) == snapshot
            # Rehydrated metadata must retain template semantics, not only rendered text.
            assert snapshot.model_info.instruction_template is not None
            checkpoint = await runtime._checkpointer.aget_tuple(
                runtime._graph_config(events[-1].turn_id)
            )
            restored = checkpoint.checkpoint["channel_values"]["turn_model_settings"]
            assert restored == snapshot
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
