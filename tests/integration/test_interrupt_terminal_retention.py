import asyncio
import json
import os
import shlex
import sqlite3
import sys
from dataclasses import FrozenInstanceError

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelItemCompleted
from corki.protocol.events import TurnCancelled, TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX process/PTY fixtures")


@pytest.mark.parametrize("route", ["completed", "streaming", "nested"])
def test_interrupt_keeps_initial_terminal_and_next_turn_reuses_it(tmp_path, route):
    async def scenario():
        started, children, requests, events = asyncio.Event(), [], [], []
        script = "print('READY',flush=True); print('ANSWER:'+input(),flush=True)"
        command = "exec " + shlex.join([sys.executable, "-c", script])
        terminal_id = None

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) <= 2:
                    name, args = (
                        (
                            "exec_command",
                            {"cmd": command, "login": False, "tty": True, "yield_time_ms": 30000},
                        )
                        if len(requests) == 1
                        else (
                            "write_stdin",
                            {"session_id": terminal_id, "chars": "kept\n", "yield_time_ms": 1000},
                        )
                    )
                    call = ToolCall(new_tool_call_id(), name, args)
                    if route == "nested":
                        call = ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments=f"text(await tools.{name}({json.dumps(args)}));",
                        )
                    item = ToolCallItem(call, turn, step)
                    if route == "streaming":
                        yield ModelItemCompleted(item)
                    yield ModelCompleted((item,))
                else:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert "ANSWER:kept" in result.content and not result.is_error
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_mode="code_mode" if route == "nested" else "direct",
            ),
            database_path=tmp_path / "retained.db",
            model=Model(),
        )
        manager = runtime._process_manager
        wait = manager._wait_session
        start = manager._start_session

        async def count_start(*args, **kwargs):
            child = await start(*args, **kwargs)
            children.append(child)
            return child

        manager._start_session = count_start

        async def capture(session, seconds):
            if manager._sessions.get(session.id) is session:
                started.set()
            return await wait(session, seconds)

        manager._wait_session = capture

        async def consume():
            async for event in runtime.stream("start terminal"):
                events.append(event)

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(started.wait(), 3)
            await runtime.cancel_active()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert isinstance(events[-1], TurnCancelled)
            child = children[0]
            assert child.process.returncode is None, "interrupt must retain the published child"
            assert manager._sessions.get(child.id) is child
            terminal_id = child.id
            terminals = await runtime.list_background_terminals()
            assert len(terminals) == 1
            info = terminals[0]
            assert info.process_id == terminal_id and info.command == command
            assert info.cwd == tmp_path and info.item_id
            with sqlite3.connect(tmp_path / "retained.db") as db:
                assert db.execute(
                    "SELECT call_id FROM tool_executions WHERE tool_name='exec_command'"
                ).fetchall() == [(info.item_id,)]

            with pytest.raises(FrozenInstanceError):
                info.command = "changed"
            events = [e async for e in runtime.stream("continue terminal")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert child.process.returncode == 0
            assert await runtime.list_background_terminals() == ()
            assert len(requests) == 3 and len(children) == 1
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()
            assert not manager._sessions

        cold = LangGraphRuntime.create(
            settings=runtime._settings,
            database_path=tmp_path / "retained.db",
            model=Model(),
            thread_id=runtime.thread_id,
        )
        cold_start = cold._process_manager._start_session

        async def count_cold(*args, **kwargs):
            child = await cold_start(*args, **kwargs)
            children.append(child)
            return child

        cold._process_manager._start_session = count_cold
        try:
            assert await cold.list_background_terminals() == ()
            events = [e async for e in cold.stream("recall")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 4 and len(children) == 1
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("route", ["completed", "streaming", "nested"])
@pytest.mark.parametrize("phase", ["poll", "model"])
def test_interrupt_keeps_previous_turn_terminal(tmp_path, route, phase):
    async def scenario():
        mode, terminal_id = "launch", None
        started, children, events = asyncio.Event(), [], []

        class Model:
            async def stream(self, request):
                nonlocal mode
                turn, step = request.items[-1].turn_id, new_step_id()
                if mode == "model":
                    started.set()
                    await asyncio.Event().wait()
                if mode in {"launch", "poll"}:
                    name, args = (
                        (
                            "exec_command",
                            {"cmd": "exec sleep 60", "login": False, "yield_time_ms": 0},
                        )
                        if mode == "launch"
                        else ("write_stdin", {"session_id": terminal_id, "yield_time_ms": 30000})
                    )
                    mode = "answer"
                    call = ToolCall(new_tool_call_id(), name, args)
                    if route == "nested":
                        call = ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments=f"text(await tools.{name}({json.dumps(args)}));",
                        )
                    item = ToolCallItem(call, turn, step)
                    if route == "streaming":
                        yield ModelItemCompleted(item)
                    yield ModelCompleted((item,))
                else:
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_mode="code_mode" if route == "nested" else "direct",
            ),
            database_path=tmp_path / "previous.db",
            model=Model(),
        )
        manager = runtime._process_manager
        start, wait = manager._start_session, manager._wait_session

        async def capture(*args, **kwargs):
            child = await start(*args, **kwargs)
            children.append(child)
            return child

        async def observe(session, seconds):
            if seconds == 30:
                started.set()
            return await wait(session, seconds)

        manager._start_session, manager._wait_session = capture, observe
        task = None
        try:
            first = [e async for e in runtime.stream("launch")]
            assert isinstance(first[-1], TurnCompleted), first[-1]
            (info,) = await runtime.list_background_terminals()
            terminal_id = info.process_id
            mode = phase

            async def consume():
                async for event in runtime.stream("interrupt next turn"):
                    events.append(event)

            task = asyncio.create_task(consume())
            await asyncio.wait_for(started.wait(), 3)
            await runtime.cancel_active()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert isinstance(events[-1], TurnCancelled)
            assert children[0].process.returncode is None
            assert await runtime.list_background_terminals() == (info,)
            assert not children[0].interaction_lock.locked()
            mode = "answer"
            resumed = [e async for e in runtime.stream("continue")]
            assert isinstance(resumed[-1], TurnCompleted) and len(children) == 1
            assert await runtime.terminate_background_terminal(terminal_id)
            assert children[0].cleanup_task.done()
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())
