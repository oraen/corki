import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelRequest, ModelTextDelta
from corki.models.types import ModelEvent
from corki.protocol.events import (
    AssistantMessageInterrupted,
    AssistantTextDelta,
    RealtimeInputAccepted,
    TurnCompleted,
)
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    UserMessageItem,
    new_step_id,
)


class SteerableModel:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self.release_first = asyncio.Event()

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        turn_id = request.items[-1].turn_id
        if len(self.requests) == 1:
            yield ModelTextDelta("stale prefix")
            await self.release_first.wait()
            yield ModelCompleted((AssistantMessageItem("stale", turn_id, new_step_id()),))
            return
        yield ModelTextDelta("fresh answer")
        yield ModelCompleted((AssistantMessageItem("fresh answer", turn_id, new_step_id()),))

    async def aclose(self) -> None:
        return None


@pytest.mark.parametrize("initial_skill", [False, True])
def test_live_input_waits_for_sampling_and_rebuilds_next_context(
    tmp_path: Path, initial_skill
) -> None:
    skill = tmp_path / ".corki" / "skills" / "steering" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: steering\ndescription: steering test\n---\n\nSTEERING_SKILL_BODY\n",
        encoding="utf-8",
    )
    model = SteerableModel()
    runtime = LangGraphRuntime.create(
        settings=CorkiSettings(working_directory=tmp_path),
        database_path=tmp_path / ".corki" / "sessions.db",
        model=model,
    )

    async def scenario() -> list[object]:
        events = []
        initial = "initial $steering request" if initial_skill else "initial request"
        async for event in runtime.stream(initial, realtime=True):
            events.append(event)
            if isinstance(event, AssistantTextDelta) and event.delta == "stale prefix":
                await runtime.steer("use $steering and the new direction")
                model.release_first.set()
        await runtime.aclose()
        return events

    events = asyncio.run(scenario())

    assert not any(isinstance(event, AssistantMessageInterrupted) for event in events)
    assert any(isinstance(event, RealtimeInputAccepted) for event in events)
    assert any(
        isinstance(item, AssistantMessageItem) and item.content == "stale"
        for item in model.requests[-1].items
    )
    assert isinstance(events[-1], TurnCompleted)
    assert events[-1].final_answer == "fresh answer"
    assert len(model.requests) == 2
    assert any(
        isinstance(item, UserMessageItem) and item.content == "use $steering and the new direction"
        for item in model.requests[-1].items
    )
    steered_items = model.requests[-1].items
    selected = [
        item
        for item in steered_items
        if isinstance(item, ContextItem) and "STEERING_SKILL_BODY" in item.content
    ]
    assert len(selected) == int(initial_skill)
    if initial_skill:
        original_input = next(i for i in steered_items if isinstance(i, UserMessageItem))
        assert selected[0].source_input_id == original_input.id
        assert selected == [
            i
            for i in model.requests[0].items
            if isinstance(i, ContextItem) and "STEERING_SKILL_BODY" in i.content
        ]


def test_realtime_end_transition_is_explicit_and_not_repeated_after_cold_resume(tmp_path):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
        )
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            database_path=tmp_path / "realtime.db",
            home_path=tmp_path / "home",
            model=Model(),
        )
        thread = runtime.thread_id
        try:
            live = [e async for e in runtime.stream("live", realtime=True)]
            normal = [e async for e in runtime.stream("normal", realtime=False)]
            assert isinstance(live[-1], TurnCompleted)
            assert isinstance(normal[-1], TurnCompleted)
            stored = await runtime._repository.load_items(thread)
            starts = [
                item
                for item in requests[0].items
                if isinstance(item, ContextItem) and item.key == "realtime.active"
            ]
            assert len(starts) == 1 and "live text channel" in starts[0].content
            ends = [
                item
                for item in requests[1].items
                if isinstance(item, ContextItem)
                and item.content_kind == "corki.realtime.end_instructions"
            ]
            assert len(ends) == 1
            assert "live conversation has ended" in ends[0].content
        finally:
            await runtime.aclose()

        cold = await LangGraphRuntime.acreate(
            settings=settings,
            database_path=tmp_path / "realtime.db",
            home_path=tmp_path / "home",
            model=Model(),
            thread_id=thread,
        )
        try:
            assert [e async for e in cold.resume_pending()] == []
            later = [e async for e in cold.stream("later", realtime=False)]
            assert isinstance(later[-1], TurnCompleted)
            assert await cold._repository.load_items(thread)
            assert (await cold._repository.load_items(thread))[: len(stored)] == stored
            assert (
                sum(
                    isinstance(item, ContextItem)
                    and item.content_kind == "corki.realtime.end_instructions"
                    for item in requests[-1].items
                )
                == 1
            )
            assert (
                sum(
                    isinstance(item, ContextItem)
                    and item.content_kind == "corki.realtime.end_instructions"
                    for item in await cold._repository.load_items(thread)
                )
                == 1
            )
            assert isinstance(
                [e async for e in cold.stream("live again", realtime=True)][-1], TurnCompleted
            )
            assert (
                sum(
                    isinstance(item, ContextItem) and item.key == "realtime.active"
                    for item in await cold._repository.load_items(thread)
                )
                == 3
            )
            assert (
                sum(
                    isinstance(item, ContextItem)
                    and item.content_kind == "corki.realtime.end_instructions"
                    for item in await cold._repository.load_items(thread)
                )
                == 1
            )
        finally:
            await cold.aclose()

    asyncio.run(scenario())
