"""Durable Turn modes and retries must not follow newly published model metadata."""

import asyncio
from dataclasses import replace

import pytest
from test_code_mode import request_call
from test_model_tool_modes import mode_settings

from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted, ModelError, ModelErrorKind, ModelTextDelta
from corki.protocol.events import AssistantTextDelta, RealtimeInputAccepted, TurnCompleted
from corki.protocol.ids import new_turn_id
from corki.protocol.items import ToolCallItem, ToolResultItem, UserMessageItem
from corki.protocol.tools import ToolResult, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize("boundary", ["prepare_model_context", "call_model", "execute_tools"])
@pytest.mark.parametrize("initial", ["direct", "nested"])
def test_cold_resume_keeps_admitted_mode_then_new_turn_reads_changed_catalog(
    tmp_path, boundary, initial
):
    async def scenario():
        requests, calls = [], []
        original_nested = initial == "nested"
        new_turn = False

        class Probe:
            spec = ToolSpec("probe", "recovery proof", {})

            async def execute(self, call, context):
                calls.append(call.id)
                return ToolResult(call.id, call.name, "RECOVERY_PROOF")

        class Model:
            async def stream(self, request):
                requests.append(request)
                nested = not original_nested if new_turn else original_nested
                assert ("exec" in {t.name for t in request.tools}) is nested
                if len(requests) == 1:
                    yield request_call(
                        request,
                        "exec" if nested else "probe",
                        "text(await tools.probe({}));" if nested else {},
                    )
                else:
                    assert any(
                        isinstance(i, ToolResultItem) and "RECOVERY_PROOF" in i.content
                        for i in request.items
                    )
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        settings = mode_settings(tmp_path, initial=initial)

        async def create(selected, thread=None):
            registry = ToolRegistry()
            registry.register(Probe())
            return await LangGraphRuntime.acreate(
                settings=selected,
                registry=registry,
                model=Model(),
                database_path=tmp_path / "s.db",
                thread_id=thread,
            )

        old = await create(settings)
        try:
            await old._ensure_ready()
            thread, turn = old.thread_id, new_turn_id()
            user = UserMessageItem("ONLY_ONCE", turn)
            state = _initial_state(thread, turn, settings, user)
            await old._repository.save_turn(
                TurnRecord(
                    turn,
                    thread,
                    TurnStatus.RUNNING,
                    user.content,
                    model_settings=state["turn_model_settings"],
                )
            )

            class Sink:
                async def emit(self, event):
                    pass

            await old._compiled.ainvoke(
                state,
                config=old._graph_config(turn),
                context=GraphRunContext(events=Sink()),
                interrupt_before=[boundary],
            )
            saved = await old._compiled.aget_state(old._graph_config(turn))
            assert saved.next == (boundary,)
            assert len(requests) == (1 if boundary == "execute_tools" else 0)
            assert not calls
        finally:
            await old.aclose()
        changed = replace(
            settings,
            model_contexts=tuple(
                replace(info, tool_mode="direct" if original_nested else "code_mode_only")
                if info.model == initial
                else info
                for info in settings.model_contexts
            ),
        )
        cold = await create(changed, thread)
        try:
            events = [e async for e in cold.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 2 and len(calls) == 1
            stored = await cold._repository.load_items(thread)
            assert sum(isinstance(i, UserMessageItem) for i in stored) == 1
            assert sum(isinstance(i, ToolCallItem) for i in stored) == 1
            new_turn = True
            events = [e async for e in cold.stream("next mode")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 3 and len(calls) == 1
        finally:
            await cold.aclose()

    asyncio.run(scenario())


def test_retry_uses_selected_router_and_handler_after_model_and_registry_update(tmp_path):
    async def scenario():
        requests, calls = [], []

        class Probe:
            spec = ToolSpec("probe", "proof", {})

            def __init__(self, name):
                self.name = name

            async def execute(self, call, context):
                calls.append(self.name)
                return ToolResult(call.id, call.name, self.name)

        registry = ToolRegistry()
        owner = registry.create_owner()
        registry.replace_owned(owner, (Probe("ORIGINAL_PROOF"),))

        class Model:
            async def stream(self, request):
                requests.append(request)
                assert "exec" in {t.name for t in request.tools}
                if len(requests) == 1:
                    result = await runtime.update_turn_settings(
                        runtime._active_run.turn_id, model="direct"
                    )
                    assert result.status == "applied"
                    registry.replace_owned(owner, (Probe("REPLACEMENT"),))
                    raise ModelError(
                        "transient transport failure",
                        kind=ModelErrorKind.TRANSPORT,
                        retryable=True,
                        retry_after_seconds=0,
                    )
                if len(requests) == 2:
                    yield request_call(request, "exec", "text(await tools.probe({}));")
                else:
                    assert any(
                        isinstance(i, ToolResultItem) and "ORIGINAL_PROOF" in i.content
                        for i in request.items
                    )
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=mode_settings(tmp_path, step_model_switching=True),
            registry=registry,
            model=Model(),
            database_path=tmp_path / "s.db",
        )
        try:
            events = [e async for e in runtime.stream("retry original step")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert [r.model for r in requests] == ["nested", "nested", "direct"]
            assert requests[0].tools == requests[1].tools
            assert requests[0].items == requests[1].items
            assert calls == ["ORIGINAL_PROOF"]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("initial", ["direct", "nested"])
def test_realtime_steering_keeps_admitted_mode_after_sampling_model_change(tmp_path, initial):
    async def scenario():
        requests, called = [], []
        release = asyncio.Event()
        nested = initial == "nested"
        destination = "direct" if nested else "nested"

        class Probe:
            spec = ToolSpec("probe", "steering proof", {})

            async def execute(self, call, context):
                called.append(call.id)
                return ToolResult(call.id, call.name, "STEER_PROOF")

        class Model:
            async def stream(self, request):
                requests.append(request)
                assert ("exec" in {t.name for t in request.tools}) is nested
                if len(requests) == 1:
                    yield ModelTextDelta("STEER_NOW")
                    await release.wait()
                    yield ModelCompleted(())
                elif len(requests) == 2:
                    assert any(
                        isinstance(i, UserMessageItem) and i.content == "NEW_DIRECTION"
                        for i in request.items
                    )
                    yield request_call(
                        request,
                        "exec" if nested else "probe",
                        "text(await tools.probe({}));" if nested else {},
                    )
                else:
                    assert any(
                        isinstance(i, ToolResultItem) and "STEER_PROOF" in i.content
                        for i in request.items
                    )
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Probe())
        runtime = await LangGraphRuntime.acreate(
            settings=mode_settings(tmp_path, initial=initial, step_model_switching=True),
            registry=registry,
            model=Model(),
            database_path=tmp_path / "s.db",
        )
        try:
            events = []
            async for event in runtime.stream("INITIAL", realtime=True):
                events.append(event)
                if isinstance(event, AssistantTextDelta) and event.delta == "STEER_NOW":
                    updated = await runtime.update_turn_settings(event.turn_id, model=destination)
                    assert updated.status == "applied"
                    await runtime.steer("NEW_DIRECTION")
                    release.set()
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert sum(isinstance(e, RealtimeInputAccepted) for e in events) == 1
            assert [r.model for r in requests] == [initial, destination, destination]
            assert len(called) == 1
        finally:
            release.set()
            await runtime.aclose()

    asyncio.run(scenario())
