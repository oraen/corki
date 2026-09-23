import asyncio
import json
import os
import shlex
import sys
from contextlib import suppress

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCancelled, TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools.builtin import process as module
from corki.tools.builtin.process import ProcessManager

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX shell/PTY fixture")


@pytest.mark.parametrize("tty", [False, True])
@pytest.mark.parametrize("closing", [False, True, "cancel_close"])
def test_os_spawn_before_handoff_is_owned_during_cancel_or_close(
    tmp_path, monkeypatch, tty, closing
):
    async def scenario():
        manager = ProcessManager()
        created, release = asyncio.Event(), asyncio.Event()
        children = []
        original = module._spawn

        async def gated(*args, **kwargs):
            child = await original(*args, **kwargs)
            children.append(child)
            created.set()
            await release.wait()
            return child

        monkeypatch.setattr(module, "_spawn", gated)
        command = "exec " + shlex.join([sys.executable, "-c", "import time; time.sleep(20)"])
        call = asyncio.create_task(
            manager.execute(
                command, cwd=tmp_path, yield_seconds=10, timeout_seconds=30, tty=tty, login=False
            )
        )
        close = None
        try:
            await asyncio.wait_for(created.wait(), 2)
            if closing:
                close = asyncio.create_task(manager.terminate_all())
                await asyncio.sleep(0)
                if closing == "cancel_close":
                    close.cancel()
                    await asyncio.sleep(0)
                    close.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await manager.execute(
                        "exit 0", cwd=tmp_path, yield_seconds=0, timeout_seconds=1
                    )
            else:
                call.cancel()
                await asyncio.sleep(0)
                call.cancel()
            await asyncio.sleep(0)
            assert not (close if closing else call).done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(call, 2)
            if close is not None:
                if closing == "cancel_close":
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(close, 2)
                else:
                    await asyncio.wait_for(close, 2)
            assert all(child.returncode is not None for child in children)
            assert not manager._sessions
        finally:
            release.set()
            call.cancel()
            await asyncio.gather(call, return_exceptions=True)
            if close is not None:
                await asyncio.gather(close, return_exceptions=True)
            for child in children:
                await manager._terminate(child)
                child._transport.close()
            await manager.terminate_all()

    asyncio.run(scenario())


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("action", ["cancel", "close", "reader", "timeout"])
def test_runtime_startup_and_background_failure_do_not_replay(
    tmp_path, monkeypatch, nested, action
):
    async def scenario():
        requests, children, events = [], [], []
        created, release = asyncio.Event(), asyncio.Event()
        original_spawn = module._spawn
        cancelling = action in ("cancel", "close")

        async def spawn(*args, **kwargs):
            child = await original_spawn(*args, **kwargs)
            children.append(child)
            created.set()
            if cancelling:
                await release.wait()
            return child

        monkeypatch.setattr(module, "_spawn", spawn)
        command = "exec " + shlex.join([sys.executable, "-c", "import time; time.sleep(20)"])

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    args = {"cmd": command, "login": False, "yield_time_ms": 10000}
                    call = (
                        ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments=f"await tools.exec_command({json.dumps(args)});",
                        )
                        if nested
                        else ToolCall(new_tool_call_id(), "exec_command", args)
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    if not cancelling:
                        result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                        assert result.is_error and f"injected {action} failure" in result.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            command_timeout_seconds=30 if action == "timeout" else None,
            tool_mode="code_mode" if nested else "direct",
        )

        async def create(thread=None):
            return await LangGraphRuntime.acreate(
                settings=settings,
                database_path=tmp_path / "runtime.db",
                model=Model(),
                thread_id=thread,
            )

        runtime = await create()
        manager = runtime._process_manager
        execute_tasks = []
        execute = manager.execute

        async def capture_execute(*args, **kwargs):
            execute_tasks.append(asyncio.current_task())
            return await execute(*args, **kwargs)

        manager.execute = capture_execute
        original_stop = manager._terminate
        if not cancelling:

            async def fail(*args):
                raise OSError(f"injected {action} failure")

            if action == "reader":
                monkeypatch.setattr(manager, "_read_output", fail)
            else:
                original_timeout = manager._enforce_timeout

                async def timeout(session, seconds):
                    await original_timeout(session, 0.01)

                monkeypatch.setattr(manager, "_enforce_timeout", timeout)
                monkeypatch.setattr(manager, "_terminate", fail)

        async def consume():
            async for event in runtime.stream("run"):
                events.append(event)

        consumer = asyncio.create_task(consume())
        closing = None
        try:
            await asyncio.wait_for(created.wait(), 3)
            if cancelling:
                closing = asyncio.create_task(
                    runtime.cancel_active() if action == "cancel" else runtime.aclose()
                )
                # Cancellation must reach the worker, but cannot abandon spawn.
                async with asyncio.timeout(3):
                    while not execute_tasks or not execute_tasks[0].cancelling():
                        await asyncio.sleep(0.005)
                assert not consumer.done()
                if action == "close":
                    assert not closing.done()
                release.set()
                await asyncio.wait_for(closing, 3)
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(consumer, 3)
                assert sum(isinstance(e, TurnCancelled) for e in events) == 1
            else:
                await asyncio.wait_for(consumer, 3)
                assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(children) == 1 and not manager._sessions and not manager._starting
            if action != "timeout":
                assert children[0].returncode is not None
        finally:
            release.set()
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            if closing is not None:
                await asyncio.gather(closing, return_exceptions=True)
            for child in children:
                await original_stop(child)
                child._transport.close()
            await runtime.aclose()

        cold = await create(runtime.thread_id)
        try:
            cold_events = [event async for event in cold.stream("continue")]
            assert isinstance(cold_events[-1], TurnCompleted)
            assert len(children) == 1  # Neither recovery nor errors replay the command.
            assert len(requests) == (2 if cancelling else 3)
        finally:
            await cold.aclose()

    asyncio.run(scenario())


def test_published_session_survives_observer_cancel_and_poll_failure_wakes(tmp_path, monkeypatch):
    async def scenario():
        manager = ProcessManager()
        original_reader = manager._read_output
        reading, fail_now = asyncio.Event(), asyncio.Event()

        async def reader(session):
            reading.set()
            await fail_now.wait()
            raise OSError("poll reader failure")

        monkeypatch.setattr(manager, "_read_output", reader)
        command = "exec " + shlex.join([sys.executable, "-c", "import time; time.sleep(20)"])
        call = asyncio.create_task(
            manager.execute(
                command,
                cwd=tmp_path,
                login=False,
                yield_seconds=10,
                timeout_seconds=30,
            )
        )
        try:
            await asyncio.wait_for(reading.wait(), 2)
            # Startup handoff precedes this initial observation wait.
            async with asyncio.timeout(2):
                while manager._starting:
                    await asyncio.sleep(0)
            call.cancel()
            with pytest.raises(asyncio.CancelledError):
                await call
            session = next(iter(manager._sessions.values()))
            assert session.process.returncode is None
            poll = asyncio.create_task(manager.write_stdin(session.id, "", yield_seconds=10))
            fail_now.set()
            with pytest.raises(OSError, match="poll reader failure"):
                await asyncio.wait_for(poll, 2)
            assert session.process.returncode is not None and not manager._sessions
        finally:
            call.cancel()
            await asyncio.gather(call, return_exceptions=True)
            await manager.terminate_all()
        # Closing is a generation boundary, not permanent manager disposal.
        monkeypatch.setattr(manager, "_read_output", original_reader)
        observed = await manager.execute(
            "exit 0",
            cwd=tmp_path,
            login=False,
            yield_seconds=1,
            timeout_seconds=5,
        )
        assert observed.exit_code == 0 and observed.session_id is None

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["reader", "timeout"])
def test_background_failure_wakes_initial_observation(tmp_path, monkeypatch, failure):
    async def scenario():
        manager = ProcessManager()
        original_stop = manager._terminate
        children = []
        original_spawn = module._spawn

        async def capture(*args, **kwargs):
            child = await original_spawn(*args, **kwargs)
            children.append(child)
            return child

        async def fail(*args):
            raise OSError(f"injected {failure} failure")

        monkeypatch.setattr(module, "_spawn", capture)
        if failure == "reader":
            monkeypatch.setattr(manager, "_read_output", fail)
        else:
            monkeypatch.setattr(manager, "_terminate", fail)
        command = "exec " + shlex.join([sys.executable, "-c", "import time; time.sleep(20)"])
        task = asyncio.create_task(
            manager.execute(
                command,
                cwd=tmp_path,
                yield_seconds=10,
                timeout_seconds=0.01 if failure == "timeout" else 30,
                login=False,
            )
        )
        try:
            with pytest.raises(OSError, match=f"injected {failure} failure"):
                await asyncio.wait_for(asyncio.shield(task), 1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            for child in children:
                await original_stop(child)
                child._transport.close()
            with suppress(OSError):
                await manager.terminate_all()

    asyncio.run(scenario())
