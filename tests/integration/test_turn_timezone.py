import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest

from corki.config import CorkiSettings
from corki.context import local_time
from corki.context.history import active_history
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted, ModelTextDelta
from corki.protocol.events import AssistantTextDelta, TurnCompleted
from corki.protocol.ids import new_tool_call_id, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


def environment_items(request):
    return [
        item
        for item in request.items
        if isinstance(item, ContextItem) and item.key == "environment.primary"
    ]


@pytest.mark.parametrize("next_day", [False, True])
def test_turn_keeps_system_timezone_but_refreshes_date(tmp_path, monkeypatch, next_day):
    monkeypatch.setattr(local_time, "timezone_name", lambda: "Europe/Paris")
    clock = [8, 0]
    monkeypatch.setattr(
        "corki.context.builder.datetime",
        SimpleNamespace(
            now=lambda: SimpleNamespace(
                astimezone=lambda: datetime(
                    2026, 9, clock[0], tzinfo=timezone(timedelta(hours=clock[1]))
                )
            )
        ),
    )

    async def scenario():
        requests = []

        class Advance:
            spec = ToolSpec("advance", "advance fixture clock and local offset", {})

            async def execute(self, call, context):
                clock[:] = [9 if next_day else 8, 8]
                return ToolResult(call.id, call.name, "changed")

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                item = (
                    ToolCallItem(ToolCall(new_tool_call_id(), "advance", {}), turn, step)
                    if len(requests) == 1
                    else AssistantMessageItem("done", turn, step)
                )
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Advance())
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=Model(),
        )
        try:
            events = [e async for e in runtime.stream("advance and continue")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            (first,) = environment_items(requests[0])
            latest = environment_items(requests[-1])
            first_timezone = ElementTree.fromstring(first.content).findtext("timezone")
            assert first_timezone not in {"UTC", "UTC+08:00"}
            assert len(latest) == (2 if next_day else 1)
            assert ElementTree.fromstring(latest[-1].content).findtext("timezone") == first_timezone
            if next_day:
                assert (
                    ElementTree.fromstring(latest[-1].content).findtext("current_date")
                    == "2026-09-09"
                )
                assert "<cwd>" not in latest[-1].content
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("legacy", [False, True])
def test_timezone_checkpoint_freeze_and_legacy_first_prepare(tmp_path, monkeypatch, legacy):
    zone, captures = ["Europe/Paris"], []

    def capture():
        captures.append(zone[0])
        return zone[0]

    monkeypatch.setattr(local_time, "timezone_name", capture)

    async def scenario():
        requests = []
        settings = CorkiSettings(working_directory=tmp_path, skills_enabled=False)

        class Advance:
            spec = ToolSpec("advance", "change fixture OS zone", {})

            async def execute(self, call, context):
                zone[0] = "America/Los_Angeles"
                return ToolResult(call.id, call.name, "changed")

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                item = (
                    ToolCallItem(ToolCall(new_tool_call_id(), "advance", {}), turn, step)
                    if len(requests) in {2, 3}
                    else AssistantMessageItem("done", turn, step)
                )
                yield ModelCompleted((item,))

            async def aclose(self):
                pass

        class Sink:
            async def emit(self, event):
                pass

        async def create(thread_id=None):
            registry = ToolRegistry()
            registry.register(Advance())
            return await LangGraphRuntime.acreate(
                settings=settings,
                database_path=tmp_path / "sessions.db",
                registry=registry,
                model=Model(),
                thread_id=thread_id,
            )

        runtime = await create()
        try:
            events = [e async for e in runtime.stream("first turn")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            thread, turn = runtime.thread_id, new_turn_id()
            user = UserMessageItem("pending", turn)
            await runtime._repository.save_turn(
                TurnRecord(turn, thread, TurnStatus.RUNNING, user.content)
            )
            initial = _initial_state(thread, turn, settings, user)
            if legacy:
                initial.pop("timezone_name")
            boundary = "prepare_model_context" if legacy else "call_model"
            config = runtime._graph_config(turn)
            await runtime._compiled.ainvoke(
                initial,
                config=config,
                context=GraphRunContext(events=Sink()),
                interrupt_before=[boundary],
            )
            checkpoint = await runtime._compiled.aget_state(config)
            assert checkpoint.next == (boundary,)
            assert ("timezone_name" not in checkpoint.values) == legacy
            prepared = await runtime._repository.load_items(thread)
            await runtime.aclose()
            zone[0] = "Asia/Shanghai"
            captures.clear()
            runtime = await create(thread)
            events = [e async for e in runtime.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            expected = "Asia/Shanghai" if legacy else "Europe/Paris"
            assert captures == ([expected] if legacy else [])
            assert len(requests) == 4
            if not legacy:
                visible = tuple(
                    i for i in prepared if not isinstance(i, ContextItem) or not i.is_snapshot_only
                )
                expected_items = visible  # Ordinary replay adds no private metadata.
                assert requests[1].items == expected_items
                assert all(
                    item.response_item_metadata_json is None
                    for item in visible
                    if isinstance(item, AssistantMessageItem)
                )
            for request in requests[1:]:
                assert (
                    ElementTree.fromstring(environment_items(request)[-1].content).findtext(
                        "timezone"
                    )
                    == expected
                )
            checkpoint = await runtime._compiled.aget_state(config)
            assert checkpoint.values["timezone_name"] == expected
            events = [e async for e in runtime.stream("new turn")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert captures[-1] == "America/Los_Angeles"
            assert (
                ElementTree.fromstring(environment_items(requests[-1])[-1].content).findtext(
                    "timezone"
                )
                == captures[-1]
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["steer", "compact"])
def test_steering_and_token_budget_compaction_use_admitted_timezone(tmp_path, monkeypatch, action):
    captures = []
    release = asyncio.Event()

    def capture():
        value = ("Europe/Paris", "Asia/Shanghai", "America/Los_Angeles")[min(len(captures), 2)]
        captures.append(value)
        return value

    monkeypatch.setattr(local_time, "timezone_name", capture)

    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                if action == "steer" and len(requests) == 1:
                    yield ModelTextDelta("old prefix")
                    await release.wait()
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                token_budget_enabled=action == "compact",
            ),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=Model(),
        )
        try:
            events = []
            async for event in runtime.stream("first", realtime=action == "steer"):
                events.append(event)
                if isinstance(event, AssistantTextDelta) and event.delta == "old prefix":
                    await runtime.steer("new direction")
                    release.set()
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert captures == ["Europe/Paris"]
            if action == "compact":
                before = await runtime._repository.load_items(runtime.thread_id)
                events = [e async for e in runtime.compact()]
                assert isinstance(events[-1], TurnCompleted), events[-1]
                assert captures == ["Europe/Paris", "Asia/Shanghai"]
                items = await runtime._repository.load_items(runtime.thread_id)
                assert items[: len(before)] == before
                assert environment_items(requests[-1]) == environment_items(requests[0])
                assert not any(
                    isinstance(i, ContextItem) and i.key == "environment.primary"
                    for i in active_history(items)
                )
                checkpoint = await runtime._compiled.aget_state(
                    runtime._graph_config(events[-1].turn_id)
                )
                assert checkpoint.values["timezone_name"] == "Asia/Shanghai"
                events = [e async for e in runtime.stream("following ordinary turn")]
                assert isinstance(events[-1], TurnCompleted), events[-1]
                assert captures == ["Europe/Paris", "Asia/Shanghai", "America/Los_Angeles"]
                (current,) = environment_items(requests[-1])
                assert (
                    ElementTree.fromstring(current.content).findtext("timezone")
                    == "America/Los_Angeles"
                )
                assert (await runtime._repository.load_items(runtime.thread_id))[
                    : len(items)
                ] == items
            else:
                assert len(requests) == 2
                assert environment_items(requests[0]) == environment_items(requests[1])
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
