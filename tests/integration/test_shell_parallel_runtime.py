"""Actual builtin metadata must reach completed, streaming and nested dispatch."""

import asyncio
import json
import os
import shlex
import sqlite3
import sys

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted, ModelItemCompleted
from corki.protocol.events import TurnCancelled, TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX child and PTY fixtures")
ROUTES = ("completed", "streaming", "nested")


def command(script):
    return "exec " + shlex.join([sys.executable, "-c", script])


class CallsModel:
    def __init__(self, route, calls, check, before_completed=None):
        self.route, self.calls, self.check = route, calls, check
        self.before_completed = before_completed
        self.requests = []

    async def stream(self, request):
        self.requests.append(request)
        turn, step = request.items[-1].turn_id, new_step_id()
        if len(self.requests) == 1:
            calls = self.calls
            if self.route == "nested":
                code = (
                    "text(await Promise.all(["
                    + ",".join(f"tools.{name}({json.dumps(args)})" for name, args in calls)
                    + "]));"
                )
                items = (
                    ToolCallItem(
                        ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments=code,
                        ),
                        turn,
                        step,
                    ),
                )
            else:
                items = tuple(
                    ToolCallItem(ToolCall(new_tool_call_id(), name, args), turn, step)
                    for name, args in calls
                )
            if self.route == "streaming":
                for item in items:
                    yield ModelItemCompleted(item)
                if self.before_completed:
                    await self.before_completed()
            yield ModelCompleted(items)
        else:
            self.check([item for item in request.items if isinstance(item, ToolResultItem)])
            yield ModelCompleted((AssistantMessageItem("done", turn, step),))

    async def aclose(self):
        pass


def runtime_for(tmp_path, route, model, thread=None):
    return LangGraphRuntime.create(
        settings=CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            tool_mode="code_mode" if route == "nested" else "direct",
        ),
        database_path=tmp_path / "parallel.db",
        model=model,
        thread_id=thread,
    )


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("operation", ["exec_command", "write_stdin"])
@pytest.mark.parametrize("exit_code", [0, 7])
def test_independent_shell_calls_overlap_and_recover_without_replay(
    tmp_path, route, operation, exit_code
):
    async def scenario():
        both, release = asyncio.Event(), tmp_path / "release"
        entered, children, calls = [], [], []

        def check(results):
            assert all(not result.is_error for result in results)
            if route == "nested":
                text = results[-1].content
                assert text.index("RESULT_0") < text.index("RESULT_1"), text
            else:
                assert len(results) == 2
                for index, result in enumerate(results):
                    assert f"RESULT_{index}" in result.content, result.content

        async def before_completed():
            # Streamed handlers must start before response.completed.
            await asyncio.wait_for(both.wait(), 3)

        model = CallsModel(route, calls, check, before_completed)
        runtime = runtime_for(tmp_path, route, model)
        manager = runtime._process_manager
        start, write = manager._start_session, manager._write_stdin

        async def join(session):
            entered.append(session)
            if len(entered) == 2:
                assert all(child.process.returncode is None for child in entered)
                release.touch()
                both.set()
            await asyncio.wait_for(both.wait(), 2)

        async def capture(*args, **kwargs):
            session = await start(*args, **kwargs)
            children.append(session)
            if operation == "exec_command":
                await join(session)
            return session

        async def gated_write(session, chars, seconds):
            await join(session)
            return await write(session, chars, seconds)

        manager._start_session = capture
        manager._write_stdin = gated_write
        try:
            for index in range(2):
                script = f"""
import time
from pathlib import Path
print('READY', flush=True)
while not Path({str(release)!r}).exists(): time.sleep(.005)
if {operation == "write_stdin"!r}: input()
print('RESULT_{index}', flush=True)
raise SystemExit({exit_code})
"""
                args = {"cmd": command(script), "login": False, "yield_time_ms": 1000}
                if operation == "write_stdin":
                    initial = await manager.execute(
                        args["cmd"], cwd=tmp_path, login=False, tty=True, yield_seconds=0.1
                    )
                    # A valid 100ms yield may precede child startup output.
                    # Overlap and completion are asserted at the joined writes,
                    # final RESULTs, exit status, ledger and cold reopen below.
                    assert initial.session_id and initial.exit_code is None
                    calls.append(
                        (
                            operation,
                            {
                                "session_id": initial.session_id,
                                "chars": "go\n",
                                "yield_time_ms": 1000,
                            },
                        )
                    )
                else:
                    calls.append((operation, args))
            events = [event async for event in runtime.stream("run both")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(entered) == len(children) == 2 and not manager._sessions
            assert all(child.process.returncode == exit_code for child in children)
            with sqlite3.connect(tmp_path / "parallel.db") as db:
                rows = db.execute(
                    "SELECT result_json FROM tool_executions WHERE tool_name=?", (operation,)
                ).fetchall()
            assert len(rows) == 2
            for (encoded,) in rows:
                result = json.loads(encoded)
                assert not result["is_error"]
                assert result["code_mode_output"]["value"]["exit_code"] == exit_code
        finally:
            await runtime.aclose()
        cold = runtime_for(tmp_path, route, model, runtime.thread_id)
        cold_start = cold._process_manager._start_session

        async def count_replay(*args, **kwargs):
            children.append(await cold_start(*args, **kwargs))
            return children[-1]

        cold._process_manager._start_session = count_replay
        try:
            events = [event async for event in cold.stream("continue")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(model.requests) == 3 and len(children) == 2
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("route", ROUTES)
def test_parallel_stdin_calls_keep_same_session_lock(tmp_path, route):
    async def scenario():
        admitted, release = asyncio.Event(), asyncio.Event()
        public_calls, locked_calls, calls = [], [], []

        def check(results):
            assert all(not item.is_error for item in results)
            text = "\n".join(item.content for item in results)
            assert "FIRST_" + locked_calls[0].strip() in text
            assert "SECOND_" + locked_calls[1].strip() in text

        model = CallsModel(route, calls, check)
        runtime = runtime_for(tmp_path, route, model)
        manager = runtime._process_manager
        public, locked = manager.write_stdin, manager._write_stdin

        async def capture_public(session, chars, **kwargs):
            public_calls.append(chars)
            if len(public_calls) == 2:
                admitted.set()
            return await public(session, chars, **kwargs)

        async def capture_locked(session, chars, seconds):
            locked_calls.append(chars)
            if len(locked_calls) == 1:
                await release.wait()
            return await locked(session, chars, seconds)

        manager.write_stdin, manager._write_stdin = capture_public, capture_locked
        task = None
        try:
            initial = await manager.execute(
                command(
                    "print('READY',flush=True); a=input(); print('FIRST_'+a,flush=True); "
                    "b=input(); print('SECOND_'+b,flush=True)"
                ),
                cwd=tmp_path,
                tty=True,
                login=False,
                yield_seconds=0.1,
            )
            assert initial.session_id and "READY" in initial.output
            calls.extend(
                (
                    "write_stdin",
                    {"session_id": initial.session_id, "chars": value, "yield_time_ms": 0},
                )
                for value in ("A\n", "B\n")
            )

            async def consume():
                return [event async for event in runtime.stream("write twice")]

            task = asyncio.create_task(consume())
            await asyncio.wait_for(admitted.wait(), 3)
            # Concurrent calls need not acquire the session lock in JS source
            # order. Only one may enter; the other follows its actual admission.
            assert len(locked_calls) == 1
            release.set()
            events = await asyncio.wait_for(task, 3)
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert locked_calls == public_calls
            assert sorted(locked_calls) == ["A\n", "B\n"]
            assert not manager._sessions
        finally:
            release.set()
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("route", ROUTES)
def test_shell_parallel_declaration_preserves_exclusive_barrier(tmp_path, route):
    async def scenario():
        order = []

        class Barrier:
            spec = ToolSpec("barrier", "exclusive fixture", {"type": "object"})

            async def execute(self, call, context):
                assert order == ["start0", "finish0"]
                order.append("barrier")
                await asyncio.sleep(0.02)
                assert order == ["start0", "finish0", "barrier"]
                order.append("released")
                return ToolResult(call.id, call.name, "BARRIER")

        def check(results):
            assert all(not item.is_error for item in results)
            text = "\n".join(item.content for item in results)
            assert text.index("OUT_0") < text.index("BARRIER") < text.index("OUT_1")

        calls = [
            ("exec_command", {"cmd": "printf OUT_0", "login": False}),
            ("barrier", {}),
            ("exec_command", {"cmd": "printf OUT_1", "login": False}),
        ]
        runtime = runtime_for(tmp_path, route, CallsModel(route, calls, check))
        runtime._registry.register(Barrier())
        execute = runtime._process_manager.execute

        async def capture(cmd, **kwargs):
            index = int(cmd[-1])
            if index:
                assert order[-1] == "released"
            order.append(f"start{index}")
            observation = await execute(cmd, **kwargs)
            assert observation.exit_code == 0
            order.append(f"finish{index}")
            return observation

        runtime._process_manager.execute = capture
        try:
            events = [event async for event in runtime.stream("respect barrier")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert order == ["start0", "finish0", "barrier", "released", "start1", "finish1"]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("route", ROUTES)
def test_cancel_parallel_shells_retains_until_explicit_cleanup(tmp_path, route):
    async def scenario():
        both, children = asyncio.Event(), []
        calls = [
            (
                "exec_command",
                {
                    "cmd": command("import time; time.sleep(60)"),
                    "login": False,
                    "yield_time_ms": 30000,
                },
            )
        ] * 2
        model = CallsModel(route, calls, lambda _: pytest.fail("unexpected model step"))
        runtime = runtime_for(tmp_path, route, model)
        manager = runtime._process_manager
        wait = manager._wait_session

        async def capture(session, seconds):
            if manager._sessions.get(session.id) is session:
                children.append(session)
                if len(children) == 2:
                    both.set()
            return await wait(session, seconds)

        manager._wait_session = capture

        events = []

        async def consume():
            async for event in runtime.stream("cancel both"):
                events.append(event)

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(both.wait(), 3)
            await runtime.cancel_active()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            assert isinstance(events[-1], TurnCancelled), events[-1]
            assert len(children) == 2 and len(model.requests) == 1
            assert len(await runtime.list_background_terminals()) == 2
            assert all(child.process.returncode is None for child in children)
            await runtime.clean_background_terminals()
            assert not manager._sessions and not manager._starting
            assert all(
                child.process.returncode is not None
                and child.cleanup_task.done()
                and child.reader_task.done()
                for child in children
            )
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())
