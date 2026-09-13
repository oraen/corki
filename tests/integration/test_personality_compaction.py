"""Compaction rebuilds personality without duplicating model-switch instructions."""

import asyncio
from dataclasses import replace

import pytest
from test_thread_settings_update import Model, make_runtime, settings

from corki.config.model_context import parse_model_contexts
from corki.models import ModelCompleted
from corki.models.types import ModelUsage
from corki.protocol.events import ContextCompacted, TurnCompleted
from corki.protocol.items import AssistantMessageItem, ContextItem, new_step_id


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("next_model", ["large", "small"])
def test_personality_compaction_preserves_model_boundary(tmp_path, automatic, next_model):
    async def scenario():
        class UsageModel(Model):
            async def stream(self, request):
                self.requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),),
                    ModelUsage(180_000 if len(self.requests) == 1 else 10, 10),
                )

        configured = replace(
            settings(tmp_path),
            base_instructions="HOST",
            personality="friendly",
            auto_compact_tokens=100_000 if automatic else 190_000,
            model_contexts=parse_model_contexts(
                {
                    name: {
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
                    for name in ("large", "small")
                }
            ),
        )
        model = UsageModel()
        runtime = make_runtime(tmp_path, model, configured=configured)
        try:
            assert isinstance([e async for e in runtime.stream("first")][-1], TurnCompleted)
            prefix = await runtime._repository.load_items(runtime.thread_id)
            if not automatic:
                assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            await runtime.update_thread_settings(model=next_model)
            events = [e async for e in runtime.stream("after compact")]
            assert isinstance(events[-1], TurnCompleted)
            if automatic:
                assert any(isinstance(e, ContextCompacted) for e in events)
            assert len(model.requests) == 3
            visible = [
                i
                for i in model.requests[-1].items
                if isinstance(i, ContextItem) and "<personality_spec>" in i.content
            ]
            assert len(visible) == int(next_model == "large")
            assert all(r.instructions == "HOST" for r in model.requests)
            thread = runtime.thread_id
            stored = await runtime._repository.load_items(thread)
            assert stored[: len(prefix)] == prefix
        finally:
            await runtime.aclose()
        cold_model = Model()
        cold = make_runtime(
            tmp_path, cold_model, configured=replace(configured, model=next_model), thread=thread
        )
        try:
            assert isinstance([e async for e in cold.stream("unchanged")][-1], TurnCompleted)
            history = await cold._repository.load_items(thread)
            assert history[: len(stored)] == stored
            assert not any(
                isinstance(i, ContextItem) and i.key == "personality"
                for i in history[len(stored) :]
            )
        finally:
            await cold.aclose()

    asyncio.run(scenario())
