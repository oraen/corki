import asyncio
import re
from types import SimpleNamespace

import pytest

from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.models import ModelCompleted, ModelItemCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id, new_turn_id
from corki.protocol.items import ToolCallItem, ToolResultItem, UserMessageItem, new_step_id
from corki.protocol.tools import ToolCall, ToolConcurrency, ToolResult, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry

pytestmark = pytest.mark.skipif(not CodeModeService.available(), reason="install corki[code-mode]")


@pytest.mark.parametrize("phase", ["refresh", "build", "window"])
@pytest.mark.parametrize("streamed", [False, True])
def test_prepare_gap_queues_old_cell_calls_and_notifications(
    tmp_path, monkeypatch, phase, streamed
):
    async def scenario():
        release, called, notified = asyncio.Event(), asyncio.Event(), asyncio.Event()
        started = asyncio.Event()
        requests, executed = [], []
        registry = ToolRegistry()
        owner = registry.create_owner()

        class Hold:
            spec = ToolSpec("hold", "hold old cell", {}, concurrency=ToolConcurrency.PARALLEL)

            async def execute(self, call, context):
                started.set()
                await release.wait()
                return ToolResult(call.id, call.name, "released")

        class Ready:
            spec = ToolSpec("ready", "wait for admission", {}, concurrency=ToolConcurrency.PARALLEL)

            async def execute(self, call, context):
                await started.wait()
                return ToolResult(call.id, call.name, "ready")

        class Probe:
            def __init__(self, word):
                self.spec, self.word = ToolSpec("probe", word, {}), word

            async def execute(self, call, context):
                executed.append(self.word)
                return ToolResult(call.id, call.name, self.word)

        registry.register(Hold())
        registry.register(Ready())
        registry.replace_owned(owner, (Probe("old"),))

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn = request.items[-1].turn_id
                if len(requests) == 1:
                    registry.replace_owned(owner, (Probe("current"),))
                    item = ToolCallItem(
                        ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments="const pending = tools.hold({}); await tools.ready({}); "
                            "yield_control(); await pending; notify('gap-notification'); "
                            "text(await tools.probe({}));",
                        ),
                        turn,
                        new_step_id(),
                    )
                    if streamed:
                        yield ModelItemCompleted(item)
                    yield ModelCompleted((item,))
                elif len(requests) == 2:
                    result = next(i for i in request.items if isinstance(i, ToolResultItem))
                    cell = re.search(r"cell ID (\S+)", result.content)[1]
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(new_tool_call_id(), "wait", {"cell_id": cell}),
                                turn,
                                new_step_id(),
                            ),
                        )
                    )
                else:
                    assert executed == ["current"]
                    results = [i for i in request.items if isinstance(i, ToolResultItem)]
                    assert any("gap-notification" in i.content for i in results)
                    assert "current" in results[-1].content
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, tool_mode="code_mode_only"
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=Model(),
        )
        service = runtime._code_mode
        invoke, notify = service.invoke, service.notify

        async def track_invoke(spec, value):
            if spec.name == "probe":
                called.set()
            return await invoke(spec, value)

        async def track_notify(*args):
            notified.set()
            return await notify(*args)

        monkeypatch.setattr(service, "invoke", track_invoke)
        monkeypatch.setattr(service, "notify", track_notify)
        target, method = (
            (runtime._graph, "_refresh_tools")
            if phase == "refresh"
            else (runtime._graph._context_builder, "build")
            if phase == "build"
            else (runtime._graph._window_manager, "prepare")
        )
        original = getattr(target, method)
        checked = False

        async def prepare_gap(*args, **kwargs):
            nonlocal checked
            if len(requests) == 1 and not checked:
                checked = True
                release.set()
                await asyncio.wait_for(asyncio.gather(called.wait(), notified.wait()), 3)
                assert not service.active.is_set(), "worker must stop admission during prepare"
                assert service.dispatch is service.notifier is None
                assert not executed
            return await original(*args, **kwargs)

        monkeypatch.setattr(target, method, prepare_gap)
        try:
            events = [event async for event in runtime.stream("start")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert checked and len(requests) == 3
            assert service.dispatch is service.notifier is None
        finally:
            release.set()
            await runtime.aclose()

    asyncio.run(scenario())


def test_admitted_old_worker_keeps_its_own_turn_event_lifetime(tmp_path):
    async def scenario():
        class Probe:
            spec = ToolSpec("probe", "fixture", {})

            async def execute(self, call, context):
                return ToolResult(call.id, call.name, "late result")

        class UnusedModel:
            async def stream(self, request):
                pytest.fail("this test directly resumes an admitted worker call")
                yield

            async def aclose(self):
                pass

        class ClosedSink:
            async def emit(self, event):
                pytest.fail("old worker borrowed new Turn's event lifetime")

        registry = ToolRegistry()
        registry.register(Probe())
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, tool_mode="code_mode_only"
            ),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=UnusedModel(),
        )
        try:
            await runtime._ensure_ready()
            snapshot = registry.snapshot()
            old_state = {
                "thread_id": runtime.thread_id,
                "turn_id": new_turn_id(),
                "cwd": str(tmp_path),
            }
            context = SimpleNamespace(context=GraphRunContext(events=ClosedSink()))
            runtime._graph._activate_code_mode(old_state, context, snapshot)
            admitted_dispatch = runtime._code_mode.dispatch
            inactive = runtime._code_mode.inactive
            await runtime._code_mode.deactivate(interrupt=False)
            runtime._graph._activate_code_mode(
                {**old_state, "turn_id": new_turn_id()}, context, snapshot
            )
            assert inactive.is_set() and not runtime._code_mode.inactive.is_set()
            result = await admitted_dispatch(ToolCall(new_tool_call_id(), "probe", {}), Probe.spec)
            assert result.content == "late result"
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_cold_checkpoint_at_tools_node_starts_worker_without_resampling(tmp_path):
    async def scenario():
        requests, executed = [], []

        class Probe:
            spec = ToolSpec("probe", "fixture", {})

            async def execute(self, call, context):
                executed.append(call)
                return ToolResult(call.id, call.name, "resumed nested tool")

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(
                                    new_tool_call_id(),
                                    "exec",
                                    None,
                                    input_kind="freeform",
                                    raw_arguments="text(await tools.probe({}));",
                                ),
                                request.items[-1].turn_id,
                                new_step_id(),
                            ),
                        )
                    )
                else:
                    assert len(executed) == 1
                    assert any(
                        isinstance(i, ToolResultItem) and "resumed nested tool" in i.content
                        for i in request.items
                    )
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        class Sink:
            async def emit(self, event):
                pass

        settings = CorkiSettings(
            working_directory=tmp_path, skills_enabled=False, tool_mode="code_mode_only"
        )

        def create(thread=None):
            registry = ToolRegistry()
            registry.register(Probe())
            return LangGraphRuntime.create(
                settings=settings,
                database_path=tmp_path / "sessions.db",
                registry=registry,
                model=Model(),
                thread_id=thread,
            )

        first = create()
        try:
            await first._ensure_ready()
            thread, turn = first.thread_id, new_turn_id()
            user = UserMessageItem("resume exec", turn)
            await first._repository.save_turn(
                TurnRecord(turn, thread, TurnStatus.RUNNING, user.content)
            )
            await first._repository.append_items(thread, (user,))
            config = first._graph_config(turn)
            await first._compiled.ainvoke(
                _initial_state(thread, turn, settings, user),
                config=config,
                context=GraphRunContext(events=Sink()),
                interrupt_before=["execute_tools"],
            )
            checkpoint = await first._compiled.aget_state(config)
            assert checkpoint.next == ("execute_tools",)
            assert len(requests) == 1 and not executed
        finally:
            await first.aclose()
        second = create(thread)
        try:

            async def resume():
                return [event async for event in second.resume_pending()]

            events = await asyncio.wait_for(resume(), 5)
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 2 and len(executed) == 1
            assert not second._code_mode.active.is_set()
        finally:
            await second.aclose()

    asyncio.run(scenario())
