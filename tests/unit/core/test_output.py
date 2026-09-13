"""Interleaved messages do not share citation buffers or completion markers."""

import asyncio

import pytest

from corki.core.output import ModelOutput
from corki.models import ModelTextDelta
from corki.protocol.events import AssistantMessageCompleted, AssistantTextDelta
from corki.protocol.ids import new_thread_id, new_turn_id
from corki.protocol.items import AssistantMessageItem, new_step_id
from corki.protocol.memory import MemoryCitation


def test_interleaved_message_citation_isolation_and_once_only_completion():
    async def scenario():
        turn, thread = new_turn_id(), new_thread_id()
        first = AssistantMessageItem("first", turn, new_step_id(), memory_citation=MemoryCitation())
        second = AssistantMessageItem("second", turn, first.step_id)
        events = []

        async def emit(event):
            events.append(event)

        output = ModelOutput(thread, turn, emit)
        await output.delta(ModelTextDelta("first<corki-memory-citation>hidden", first.id))
        await output.delta(ModelTextDelta("second", second.id))
        await output.complete(second)
        await output.complete(first)
        await output.complete(second)
        assert (
            "".join(event.delta for event in events if isinstance(event, AssistantTextDelta))
            == "firstsecond"
        )
        assert [event.text for event in events if isinstance(event, AssistantMessageCompleted)] == [
            "second",
            "first",
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize("plan_mode", [False, True])
def test_legacy_unkeyed_delta_spanning_messages_is_not_duplicated(plan_mode):
    async def scenario():
        turn, thread = new_turn_id(), new_thread_id()
        first = AssistantMessageItem("first", turn, new_step_id())
        second = AssistantMessageItem("second", turn, first.step_id)
        events = []

        async def emit(event):
            events.append(event)

        output = ModelOutput(thread, turn, emit, plan_mode=plan_mode)
        await output.delta(ModelTextDelta("firstsecond"))
        await output.complete(first)
        await output.complete(second)
        assert (
            "".join(event.delta for event in events if isinstance(event, AssistantTextDelta))
            == "firstsecond"
        )
        assert [event.text for event in events if isinstance(event, AssistantMessageCompleted)] == [
            "first",
            "second",
        ]

    asyncio.run(scenario())
