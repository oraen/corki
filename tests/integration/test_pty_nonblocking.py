import asyncio
import errno
import hashlib
import json
import os
import re
import shlex
import sys

import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools.builtin import process as process_module
from corki.tools.builtin.process import ProcessManager

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Unix PTY readiness")


@pytest.mark.parametrize("transient", [None, errno.EINTR, errno.EAGAIN])
def test_master_nonblocking_and_transient_read_retry(tmp_path, monkeypatch, transient):
    async def scenario():
        manager = ProcessManager()
        read = os.read
        injected = []
        children = []
        original_reader = manager._read_pty_output

        async def capture(session):
            children.append(session)
            if transient is None:
                assert not os.get_blocking(session.pty_master_fd)
            await original_reader(session)

        def flaky(fd, count):
            if (
                transient is not None
                and children
                and fd == children[0].pty_master_fd
                and not injected
            ):
                injected.append(True)
                raise OSError(transient, "transient PTY read")
            return read(fd, count)

        monkeypatch.setattr(manager, "_read_pty_output", capture)
        monkeypatch.setattr(os, "read", flaky)
        command = "exec " + shlex.join([sys.executable, "-c", "print('complete-output')"])
        try:
            result = await asyncio.wait_for(
                manager.execute(
                    command, cwd=tmp_path, tty=True, login=False, yield_seconds=1, timeout_seconds=3
                ),
                2,
            )
            assert result.exit_code == 0 and "complete-output" in result.output
            assert transient is None or injected
        finally:
            await manager.terminate_all()

    asyncio.run(scenario())


def receiver_command(size):
    script = f"""
import hashlib, os, tty
tty.setraw(0)
print('READY', flush=True)
data = bytearray()
while len(data) < {size}:
    chunk = os.read(0, min(4096, {size} - len(data)))
    if not chunk:
        raise RuntimeError('premature EOF')
    data.extend(chunk)
print('DIGEST:' + hashlib.sha256(data).hexdigest(), flush=True)
"""
    return "exec " + shlex.join([sys.executable, "-c", script])


@pytest.mark.parametrize("partial", [False, True])
def test_large_pty_input_is_complete_without_blocking_loop(tmp_path, monkeypatch, partial):
    async def scenario():
        manager = ProcessManager()
        payload = ("甲abc\n" * 40000).encode()
        session = None
        try:
            initial = await manager.execute(
                receiver_command(len(payload)),
                cwd=tmp_path,
                tty=True,
                login=False,
                yield_seconds=0.2,
                timeout_seconds=5,
            )
            assert "READY" in initial.output and initial.session_id
            session = manager._sessions[initial.session_id]
            if partial:
                original_write = os.write
                attempts = []

                def write(fd, data):
                    if fd == session.pty_master_fd:
                        attempts.append(True)
                        if len(attempts) <= 2:
                            raise OSError(
                                errno.EINTR if len(attempts) == 1 else errno.EAGAIN, "retry"
                            )
                        data = data[:1021]
                    return original_write(fd, data)

                monkeypatch.setattr(os, "write", write)
            async with asyncio.timeout(3):
                result = await manager.write_stdin(
                    initial.session_id,
                    payload.decode(),
                    yield_seconds=1,
                )
            assert result.exit_code == 0 and hashlib.sha256(payload).hexdigest() in result.output
            assert session.writer_task.done() and not manager._sessions
        finally:
            await manager.terminate_all()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_enqueue", [False, True])
def test_saturated_writer_close_wakes_full_queue_and_removes_readiness(tmp_path, cancel_enqueue):
    async def scenario():
        manager = ProcessManager()
        script = "import tty,time; tty.setraw(0); print('READY',flush=True); time.sleep(20)"
        command = "exec " + shlex.join([sys.executable, "-c", script])
        pending = None
        try:
            initial = await manager.execute(
                command,
                cwd=tmp_path,
                tty=True,
                login=False,
                yield_seconds=0.2,
                timeout_seconds=30,
            )
            assert "READY" in initial.output and initial.session_id
            session = manager._sessions[initial.session_id]
            descriptor = session.pty_master_fd
            await manager._enqueue_pty_input(session, b"x" * (1024 * 1024))
            await asyncio.sleep(0)
            for _ in range(128):
                session.input_queue.put_nowait(b"queued")
            pending = asyncio.create_task(manager._enqueue_pty_input(session, b"blocked"))
            await asyncio.sleep(0)
            assert not pending.done()
            if cancel_enqueue:
                pending.cancel()
                await asyncio.sleep(0)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                assert not session.writer_task.done()
            async with asyncio.timeout(3):
                await manager.terminate_all()
                if not cancel_enqueue:
                    with pytest.raises((OSError, ValueError)):
                        await pending
            assert session.writer_task.done() and session.reader_task.done()
            assert session.process.returncode is not None and not manager._sessions
            assert descriptor not in asyncio.get_running_loop()._selector.get_map()
        finally:
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await manager.terminate_all()

    asyncio.run(scenario())


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("writer_fails", [False, True])
def test_runtime_pty_exact_input_and_cold_no_replay(tmp_path, monkeypatch, nested, writer_fails):
    async def scenario():
        payload = "甲abc\n" * 2000
        digest = hashlib.sha256(payload.encode()).hexdigest()
        requests = []
        effects = []
        if writer_fails:

            async def fail(descriptor, data):
                raise OSError("injected PTY writer failure")

            monkeypatch.setattr(process_module, "write_pty", fail)

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) <= 2:
                    if len(requests) == 1:
                        name, args = (
                            "exec_command",
                            {
                                "cmd": receiver_command(len(payload.encode())),
                                "login": False,
                                "tty": True,
                                "yield_time_ms": 1000,
                            },
                        )
                    else:
                        result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                        assert "READY" in result.content
                        session = re.search(
                            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", result.content
                        )[0]
                        name, args = (
                            "write_stdin",
                            {
                                "session_id": session,
                                "chars": payload,
                                "yield_time_ms": 1000,
                            },
                        )
                    call = (
                        ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments=(
                                f"const r = await tools.{name}({json.dumps(args)}); text(r);"
                            ),
                        )
                        if nested
                        else ToolCall(new_tool_call_id(), name, args)
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    if writer_fails:
                        assert result.is_error and "injected PTY writer failure" in result.content
                    else:
                        assert not result.is_error and digest in result.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            tool_mode="code_mode" if nested else "direct",
        )

        async def create(thread=None):
            runtime = await LangGraphRuntime.acreate(
                settings=settings,
                database_path=tmp_path / "pty.db",
                model=Model(),
                thread_id=thread,
            )
            manager = runtime._process_manager
            start = manager._start_session
            enqueue = manager._enqueue_pty_input

            async def counted_start(*args, **kwargs):
                effects.append("spawn")
                return await start(*args, **kwargs)

            async def counted_enqueue(session, data):
                effects.append("input")
                assert data == payload.encode()
                return await enqueue(session, data)

            manager._start_session = counted_start
            manager._enqueue_pty_input = counted_enqueue
            return runtime

        runtime = await create()
        try:
            async with asyncio.timeout(5):
                events = [e async for e in runtime.stream("roundtrip")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert not runtime._process_manager._sessions
        finally:
            await runtime.aclose()
        cold = await create(runtime.thread_id)
        try:
            events = [e async for e in cold.stream("continue")]
            assert isinstance(events[-1], TurnCompleted) and len(requests) == 4
            assert effects == ["spawn", "input"]
        finally:
            await cold.aclose()

    asyncio.run(scenario())
