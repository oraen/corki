"""Completed output prefixes become history before the last tool finishes."""

import asyncio
import sys
from pathlib import Path

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelItemCompleted
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.items import ToolCallItem, ToolResultItem, UserMessageItem, new_step_id
from corki.protocol.tools import ToolCall, ToolConcurrency, ToolResult, ToolSpec
from corki.storage import SQLiteSessionRepository
from corki.tools import ToolRegistry


@pytest.mark.parametrize("streamed", [False, True])
def test_ordered_completed_prefix_is_published_before_pending_tail(tmp_path, streamed):
    async def scenario():
        release = asyncio.Event()
        tail_started = asyncio.Event()
        requests, executed = [], []

        class Tool:
            spec = ToolSpec("probe", "fixture", {}, concurrency=ToolConcurrency.PARALLEL)

            async def execute(self, call, context):
                executed.append(call.id)
                if call.id == "tail":
                    tail_started.set()
                    await release.wait()
                return ToolResult(call.id, call.name, str(call.id))

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    step = new_step_id()
                    items = tuple(
                        ToolCallItem(ToolCall(key, "probe", {}), request.items[-1].turn_id, step)
                        for key in ("head", "tail")
                    )
                    if streamed:
                        for item in items:
                            yield ModelItemCompleted(item)
                    yield ModelCompleted(items)
                else:
                    assert [i.content for i in request.items if isinstance(i, ToolResultItem)] == [
                        "head",
                        "tail",
                    ]
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Tool())
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "sessions.db",
            registry=registry,
            model=Model(),
        )

        async def consume():
            return [event async for event in runtime.stream("run")]

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(tail_started.wait(), 2)
            async with asyncio.timeout(2):
                while True:
                    history = await runtime._repository.load_items(runtime.thread_id)
                    results = [i.content for i in history if isinstance(i, ToolResultItem)]
                    if results:
                        break
                    await asyncio.sleep(0.01)
            assert results == ["head"]
            assert not task.done() and len(requests) == 1
            release.set()
            events = await asyncio.wait_for(task, 3)
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert sorted(executed) == ["head", "tail"]
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("streamed", [False, True])
def test_process_exit_after_prefix_does_not_replay_completed_or_unknown_tools(tmp_path, streamed):
    async def scenario():
        fixture = Path(__file__).parents[1] / "fixtures" / "ordered_tool_crash.py"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(fixture),
            str(tmp_path),
            "streamed" if streamed else "batch",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
            assert process.returncode == 29, (stdout, stderr)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

        repository = SQLiteSessionRepository(tmp_path / "sessions.db")
        thread = await repository.latest_thread(tmp_path)
        turn = await repository.latest_running_turn(thread)
        assert turn is not None
        before = await repository.load_items(thread)
        assert [i.content for i in before if isinstance(i, ToolResultItem)] == ["saved head"]
        assert await repository.load_model_step(thread, turn.id, 0) is not None
        effects = (tmp_path / "effects.txt").read_text()
        assert sorted(effects.splitlines()) == ["head", "tail"]
        requests, executions = [], []

        class Tool:
            spec = ToolSpec("probe", "fixture", {}, concurrency=ToolConcurrency.PARALLEL)

            async def execute(self, call, context):
                executions.append(call.id)
                raise AssertionError("completed and unknown calls must not replay")

        class Model:
            async def stream(self, request):
                requests.append(request)
                results = [i for i in request.items if isinstance(i, ToolResultItem)]
                assert [i.call_id for i in results] == ["head", "tail"]
                assert results[0].content == "saved head" and not results[0].is_error
                assert results[1].is_error and "unknown" in results[1].content
                yield ModelCompleted(())

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Tool())
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=repository.path,
            repository=repository,
            thread_id=thread,
            registry=registry,
            model=Model(),
        )
        try:
            events = [event async for event in runtime.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 1 and executions == []
            after = await repository.load_items(thread)
            assert after[: len(before)] == before
            assert [i.call_id for i in after if isinstance(i, ToolResultItem)] == ["head", "tail"]
            assert [i.content for i in after if isinstance(i, UserMessageItem)] == ["run"]
            assert (tmp_path / "effects.txt").read_text() == effects
            assert [event async for event in runtime.resume_pending()] == []
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("committed", [False, True])
def test_prefix_publication_failure_joins_pending_tail(tmp_path, streamed, committed):
    async def scenario():
        tail_started, cleaned = asyncio.Event(), asyncio.Event()
        executed, requests, events = [], [], []

        class Repository(SQLiteSessionRepository):
            failed = False

            async def append_items(self, thread_id, items):
                if not self.failed and any(
                    isinstance(item, ToolResultItem) and item.call_id == "head" for item in items
                ):
                    self.failed = True
                    if committed:
                        await super().append_items(thread_id, items)
                    raise OSError("ordered prefix publication failed")
                return await super().append_items(thread_id, items)

        class Tool:
            spec = ToolSpec("probe", "fixture", {}, concurrency=ToolConcurrency.PARALLEL)

            async def execute(self, call, context):
                executed.append(call.id)
                if call.id == "tail":
                    tail_started.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cleaned.set()
                await tail_started.wait()
                return ToolResult(call.id, call.name, str(call.id))

        class Model:
            async def stream(self, request):
                requests.append(request)
                step = new_step_id()
                items = tuple(
                    ToolCallItem(ToolCall(key, "probe", {}), request.items[-1].turn_id, step)
                    for key in ("head", "tail")
                )
                if streamed:
                    for item in items:
                        yield ModelItemCompleted(item)
                yield ModelCompleted(items)

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Tool())
        repository = Repository(tmp_path / "sessions.db")
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=repository.path,
            repository=repository,
            registry=registry,
            model=Model(),
        )
        try:
            async with asyncio.timeout(3):
                async for event in runtime.stream("run"):
                    events.append(event)
                    if isinstance(event, TurnFailed):
                        assert cleaned.is_set(), "failure must not outlive its tool owner"
            assert isinstance(events[-1], TurnFailed), events[-1]
            assert repository.failed and cleaned.is_set()
            assert len(requests) == 1 and sorted(executed) == ["head", "tail"]
            history = await repository.load_items(runtime.thread_id)
            heads = [
                item
                for item in history
                if isinstance(item, ToolResultItem) and item.call_id == "head"
            ]
            assert len(heads) == int(committed or streamed)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
