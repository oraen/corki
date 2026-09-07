"""Shutdown must own work and persistence, independently of event consumption."""

import asyncio
import shlex
import sys
from pathlib import Path

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelTextDelta
from corki.protocol.events import TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.ids import ToolCallId
from corki.protocol.items import AssistantMessageItem, ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.sessions import TurnStatus
from corki.tools import ToolRegistry


class BlockingTool:
    spec = ToolSpec("block", "Wait for cancellation", {"type": "object"})

    def __init__(self):
        self.started = asyncio.Event()
        self.running = False

    async def execute(self, call, context):
        self.running = True
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.running = False
        return ToolResult(call.id, call.name, "unreachable")


class ShutdownModel:
    def __init__(self, site="model"):
        self.site = site
        self.started = asyncio.Event()
        self.burst_blocked = asyncio.Event()
        self.running = False
        self.closed = False

    async def stream(self, request):
        self.running = True
        self.started.set()
        try:
            turn, step = request.items[-1].turn_id, new_step_id()
            if self.site == "tool":
                yield ModelCompleted(
                    (ToolCallItem(ToolCall(ToolCallId("blocking"), "block", {}), turn, step),)
                )
            elif self.site == "answer":
                yield ModelCompleted((AssistantMessageItem("done", turn, step),))
            elif self.site == "burst":
                for index in range(10):
                    if index == 2:
                        self.burst_blocked.set()
                    yield ModelTextDelta("partial")
            else:
                await asyncio.Event().wait()
        finally:
            self.running = False

    async def aclose(self):
        self.closed = True


def runtime_for(tmp_path, model, *, registry=None):
    return LangGraphRuntime.create(
        settings=CorkiSettings(
            working_directory=tmp_path, skills_enabled=False, event_queue_size=1
        ),
        database_path=tmp_path / "shutdown.db",
        model=model,
        registry=registry or ToolRegistry(),
    )


@pytest.mark.parametrize("site", ["model", "tool"])
@pytest.mark.parametrize("realtime", [False, True])
@pytest.mark.parametrize("action", ["cancel", "close"])
def test_runtime_owns_active_model_and_tool_until_durable_terminal(
    tmp_path: Path, site, realtime, action
):
    async def scenario():
        model, tool, registry = ShutdownModel(site), BlockingTool(), ToolRegistry()
        registry.register(tool)
        runtime = runtime_for(tmp_path, model, registry=registry)
        events, close_observations = [], []
        original_close = runtime._repository.close

        async def close_repository():
            close_observations.append(
                (
                    model.running,
                    tool.running,
                    await runtime._repository.latest_running_turn(runtime.thread_id),
                )
            )
            await original_close()

        runtime._repository.close = close_repository

        async def consume():
            async for event in runtime.stream("wait", realtime=realtime):
                events.append(event)

        consumer = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(
                (model.started if site == "model" else tool.started).wait(), timeout=3
            )
            if action == "cancel":
                await runtime.cancel_active()
                await runtime.cancel_active()
            else:
                await asyncio.wait_for(runtime.aclose(), timeout=3)
                assert close_observations == [(False, False, None)], (
                    "dependencies closed before active turn and terminal persistence"
                )
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(consumer), timeout=0.5)
            assert sum(isinstance(event, TurnCancelled) for event in events) == 1
            assert not any(isinstance(event, (TurnCompleted, TurnFailed)) for event in events)
            assert not model.running and not tool.running
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["cancel", "close"])
def test_runtime_interrupt_terminates_real_exec_process_and_reader_tasks(tmp_path: Path, action):
    async def scenario():
        class ExecModel(ShutdownModel):
            async def stream(self, request):
                command = shlex.join([sys.executable, "-c", "import time; time.sleep(30)"])
                yield ModelCompleted(
                    (
                        ToolCallItem(
                            ToolCall(
                                ToolCallId("real-exec"),
                                "exec_command",
                                {"cmd": command, "yield_time_ms": 30000, "login": False},
                            ),
                            request.items[-1].turn_id,
                            new_step_id(),
                        ),
                    )
                )

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "process.db",
            model=ExecModel(),
        )
        started, processes = asyncio.Event(), []
        original_wait = runtime._process_manager._wait_at_most

        async def wait(process, seconds):
            processes.append(process)
            started.set()
            await original_wait(process, seconds)

        runtime._process_manager._wait_at_most = wait
        events = []

        async def consume():
            async for event in runtime.stream("run a process"):
                events.append(event)

        consumer = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(started.wait(), timeout=3)
            sessions = tuple(runtime._process_manager._sessions.values())
            if action == "cancel":
                await runtime.cancel_active()
            else:
                await runtime.aclose()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(consumer), timeout=3)
            assert processes and all(process.returncode is not None for process in processes)
            assert all(
                session.reader_task.done() and session.timeout_task.done() for session in sessions
            )
            assert runtime._process_manager._sessions == {}
            assert isinstance(events[-1], TurnCancelled)
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_close_cancels_and_joins_initial_turn_write_before_dependencies(tmp_path: Path):
    async def scenario():
        model = ShutdownModel()
        runtime = runtime_for(tmp_path, model)
        saving, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original_save = runtime._repository.save_turn

        async def save(turn):
            await original_save(turn)
            if turn.status is TurnStatus.RUNNING:
                saving.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    await release.wait()
                    raise

        runtime._repository.save_turn = save
        events = []

        async def consume():
            async for event in runtime.stream("start while closing"):
                events.append(event)

        consumer = asyncio.create_task(consume())
        closer = None
        try:
            await asyncio.wait_for(saving.wait(), timeout=3)
            closer = asyncio.create_task(runtime.aclose())
            await asyncio.wait_for(cancelled.wait(), timeout=3)
            assert not closer.done()
            assert not model.closed
            release.set()
            await asyncio.wait_for(closer, timeout=3)
            await asyncio.gather(consumer, return_exceptions=True)
            assert not model.started.is_set()
            assert isinstance(events[-1], TurnCancelled)
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
        finally:
            release.set()
            consumer.cancel()
            await asyncio.gather(consumer, *([closer] if closer else []), return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_concurrent_close_and_cancelled_waiter_share_uninterrupted_cleanup(tmp_path: Path):
    async def scenario():
        cleanup_started, release_cleanup = asyncio.Event(), asyncio.Event()

        class CleanupModel(ShutdownModel):
            async def stream(self, request):
                self.started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleanup_started.set()
                    await release_cleanup.wait()
                yield

        model = CleanupModel()
        runtime = runtime_for(tmp_path, model)
        events = []

        async def consume():
            async for event in runtime.stream("wait"):
                events.append(event)

        consumer = asyncio.create_task(consume())
        closers = []
        try:
            await asyncio.wait_for(model.started.wait(), timeout=3)
            first = asyncio.create_task(runtime.aclose())
            closers.append(first)
            await asyncio.wait_for(cleanup_started.wait(), timeout=3)
            second = asyncio.create_task(runtime.aclose())
            closers.append(second)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(second), timeout=0.05)
            assert not model.closed
            await runtime.cancel_active()  # must not cancel the cleanup a second time
            release_cleanup.set()
            await asyncio.wait_for(second, timeout=3)
            await asyncio.gather(consumer, return_exceptions=True)
            assert model.closed
            assert sum(isinstance(event, TurnCancelled) for event in events) == 1
        finally:
            release_cleanup.set()
            consumer.cancel()
            await asyncio.gather(consumer, *closers, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["processes", "mcp", "model", "repository"])
def test_close_failure_still_closes_other_resources_and_is_shared(tmp_path: Path, fault):
    async def scenario():
        runtime = runtime_for(tmp_path, ShutdownModel())
        await runtime._ensure_ready()
        closed = []
        targets = [
            ("processes", runtime._process_manager, "terminate_all"),
            ("mcp", runtime._mcp_manager, "aclose"),
            ("model", runtime._model, "aclose"),
            ("repository", runtime._repository, "close"),
        ]
        for name, service, method in targets:
            original = getattr(service, method)

            async def close(name=name, original=original):
                closed.append(name)
                await original()
                if name == fault:
                    raise RuntimeError(f"{name} close failed")

            setattr(service, method, close)
        with pytest.raises(RuntimeError, match=f"{fault} close failed"):
            await runtime.aclose()
        with pytest.raises(RuntimeError, match=f"{fault} close failed"):
            await runtime.aclose()
        assert closed == [name for name, _, _ in targets]
        assert runtime._checkpointer is None
        assert runtime._checkpoint_context is None

    asyncio.run(scenario())


def test_cancellation_cleanup_error_preserves_cancel_terminal_but_fails_close(tmp_path: Path):
    async def scenario():
        model = ShutdownModel()
        runtime = runtime_for(tmp_path, model)
        calls = 0
        original = runtime._process_manager.terminate_all

        async def terminate():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("process cleanup failed")
            await original()

        runtime._process_manager.terminate_all = terminate
        events = []

        async def consume():
            async for event in runtime.stream("wait"):
                events.append(event)

        consumer = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(model.started.wait(), timeout=3)
            with pytest.raises(RuntimeError, match="process cleanup failed"):
                await runtime.aclose()
            await asyncio.gather(consumer, return_exceptions=True)
            assert isinstance(events[-1], TurnCancelled)
            assert not any(isinstance(event, TurnFailed) for event in events)
            assert model.closed and calls == 2
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            with pytest.raises(RuntimeError, match="process cleanup failed"):
                await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["started", "full_queue"])
def test_close_does_not_need_paused_consumer_to_make_progress(tmp_path: Path, phase):
    async def scenario():
        model = ShutdownModel("burst")
        runtime = runtime_for(tmp_path, model)
        stream = runtime.stream("wait", realtime=True)
        try:
            await anext(stream)
            if phase == "full_queue":
                await anext(stream)
                await asyncio.wait_for(model.burst_blocked.wait(), timeout=3)
            await asyncio.wait_for(runtime.aclose(), timeout=3)
            assert not model.running
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
            assert not runtime._realtime.active
            assert not any(
                task.get_name().startswith("corki-turn-") and not task.done()
                for task in asyncio.all_tasks()
            )
            # Resuming the consumer after close must need no closed dependencies.
            events = []
            with pytest.raises(asyncio.CancelledError):
                async for event in stream:
                    events.append(event)
            assert sum(isinstance(event, TurnCancelled) for event in events) == 1
        finally:
            await stream.aclose()
            await runtime.aclose()

    asyncio.run(scenario())


def test_close_waits_for_selected_terminal_commit_without_reclassifying_it(tmp_path: Path):
    async def scenario():
        model = ShutdownModel("answer")
        runtime = runtime_for(tmp_path, model)
        saving, release = asyncio.Event(), asyncio.Event()
        original_save = runtime._repository.save_turn

        async def save(turn):
            if turn.status is TurnStatus.COMPLETED:
                saving.set()
                await release.wait()
            await original_save(turn)

        runtime._repository.save_turn = save

        async def consume():
            return [event async for event in runtime.stream("finish")]

        consumer = asyncio.create_task(consume())
        closer = None
        try:
            await asyncio.wait_for(saving.wait(), timeout=3)
            closer = asyncio.create_task(runtime.aclose())
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(closer), timeout=0.05)
            assert not model.closed
            release.set()
            await asyncio.wait_for(closer, timeout=3)
            events = await consumer
            assert isinstance(events[-1], TurnCompleted)
            assert model.closed
        finally:
            release.set()
            consumer.cancel()
            await asyncio.gather(consumer, *([closer] if closer else []), return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())
