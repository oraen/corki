import asyncio

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCancelled, TurnCompleted, TurnFailed, WarningEvent
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ContextItem, ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.skills import SkillService
from corki.tools import ToolRegistry


class BrokenMessageError(OSError):
    def __str__(self):
        raise ValueError("broken error formatter")


def install_skills(tmp_path):
    for name in ("missing-host", "available-host"):
        path = tmp_path / ".corki" / "skills" / name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nname: {name}\ndescription: fixture\n---\n\nBODY {name}", encoding="utf-8"
        )


class RecordingModel:
    def __init__(self):
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        turn, step = request.items[-1].turn_id, new_step_id()
        if len(self.requests) == 1:
            items = (ToolCallItem(ToolCall(new_tool_call_id(), "ping", {}), turn, step),)
        else:
            items = (AssistantMessageItem("done", turn, step),)
        yield ModelCompleted(items)

    async def aclose(self):
        pass


class Ping:
    spec = ToolSpec("ping", "complete a test step", {})

    async def execute(self, call, context):
        return ToolResult(call.id, call.name, "pong")


def create_runtime(tmp_path, model, **settings):
    registry = ToolRegistry()
    registry.register(Ping())
    return LangGraphRuntime.create(
        settings=CorkiSettings(working_directory=tmp_path, **settings),
        database_path=tmp_path / "sessions.db",
        home_path=tmp_path / ".corki",
        registry=registry,
        model=model,
    )


def warnings(events):
    return [event for event in events if isinstance(event, WarningEvent)]


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("READ FAILURE"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "READ FAILURE"),
        ValueError("READ FAILURE \ud800" + "x" * 8000),
        BrokenMessageError(),
    ],
    ids=["missing-file", "invalid-utf8", "bounded-message", "broken-str"],
)
def test_runtime_warns_and_omits_only_unreadable_skill(tmp_path, monkeypatch, failure):
    async def scenario():
        install_skills(tmp_path)
        reads = []
        original = SkillService.read

        def read(service, skill, relative_file="SKILL.md"):
            reads.append(skill.name)
            if skill.name == "missing-host":
                raise failure
            return original(service, skill, relative_file)

        monkeypatch.setattr(SkillService, "read", read)
        model = RecordingModel()
        runtime = create_runtime(tmp_path, model)
        try:
            events = []
            async for event in runtime.stream("Use $missing-host and $available-host"):
                events.append(event)
            assert isinstance(events[-1], TurnCompleted), events[-1]
            notices = warnings(events)
            assert len(notices) == 1
            notice = notices[0]
            assert notice.message.startswith("Failed to load skill missing-host at ")
            assert notice.thread_id == runtime.thread_id
            assert notice.turn_id == events[-1].turn_id
            assert len(notice.message) <= 4000
            notice.message.encode("utf-8")
            assert events.index(notice) < next(
                i for i, event in enumerate(events) if type(event).__name__ == "ToolCallStarted"
            )
            assert reads == ["available-host", "missing-host"]
            assert len(model.requests) == 2
            for request in model.requests:
                selected = [
                    item
                    for item in request.items
                    if isinstance(item, ContextItem) and item.source_input_id is not None
                ]
                assert len(selected) == 1
                assert "BODY available-host" in selected[0].content
                assert "Failed to load skill" not in repr(request)
                assert "READ FAILURE" not in repr(request)
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert "Failed to load skill" not in repr(stored)
            assert "READ FAILURE" not in repr(stored)
            second = [e async for e in runtime.stream("Use $missing-host again")]
            assert isinstance(second[-1], TurnCompleted)
            assert len(warnings(second)) == 1
            assert reads == ["available-host", "missing-host", "missing-host"]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_warning_backpressure_is_cancellable_without_sampling(tmp_path, monkeypatch):
    async def scenario():
        from corki.core.runtime import _QueueEventSink

        install_skills(tmp_path)
        second_emission = asyncio.Event()
        emit = _QueueEventSink.emit
        emitted = []

        async def observe(sink, event):
            if isinstance(event, WarningEvent):
                emitted.append(event)
                if len(emitted) == 2:
                    assert sink._queue.full()
                    second_emission.set()
            await emit(sink, event)

        def read(*args, **kwargs):
            raise FileNotFoundError("missing resource")

        monkeypatch.setattr(_QueueEventSink, "emit", observe)
        monkeypatch.setattr(SkillService, "read", read)
        model = RecordingModel()
        runtime = create_runtime(tmp_path, model, event_queue_size=1)
        stream = runtime.stream("Use $missing-host and $available-host")
        events = []
        try:
            events.append(await anext(stream))
            await asyncio.wait_for(second_emission.wait(), timeout=3)
            await runtime.cancel_active()

            async def drain():
                async for event in stream:
                    events.append(event)

            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(drain(), timeout=3)
            assert not model.requests
            assert len(warnings(events)) == 1  # second emission was still blocked
            assert isinstance(events[-1], TurnCancelled)
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
        finally:
            await stream.aclose()
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True], ids=["unexpected-error", "cancel"])
def test_skill_loading_does_not_downgrade_infrastructure_failure_or_cancel(
    tmp_path, monkeypatch, cancel
):
    async def scenario():
        install_skills(tmp_path)

        def read(*args, **kwargs):
            if cancel:
                raise asyncio.CancelledError
            raise RuntimeError("infrastructure failure")

        monkeypatch.setattr(SkillService, "read", read)
        model = RecordingModel()
        runtime = create_runtime(tmp_path, model)
        events = []

        async def consume():
            async for event in runtime.stream("Use $missing-host"):
                events.append(event)

        try:
            if cancel:
                with pytest.raises(asyncio.CancelledError):
                    await consume()
                assert isinstance(events[-1], TurnCancelled)
            else:
                await consume()
                assert isinstance(events[-1], TurnFailed)
                assert "infrastructure failure" in events[-1].error
            assert not warnings(events)
            assert not model.requests
            assert (
                sum(isinstance(e, (TurnCompleted, TurnCancelled, TurnFailed)) for e in events) == 1
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
