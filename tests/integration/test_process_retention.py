import asyncio
import json
import os
import re
from types import SimpleNamespace

import pytest

from corki.config import CorkiPaths, CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools.builtin.process import ProcessManager, _ProcessSession

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX owned child groups")


def test_default_resumable_lifetime_has_no_kill_timer(tmp_path):
    assert CorkiSettings(working_directory=tmp_path).command_timeout_seconds is None


def test_real_65th_process_retires_oldest_session(tmp_path):
    async def scenario():
        manager = ProcessManager()
        sessions = []
        try:
            for _ in range(65):
                result = await manager.execute(
                    "exec sleep 20",
                    cwd=tmp_path,
                    login=False,
                    yield_seconds=0,
                    timeout_seconds=30,
                )
                assert result.session_id
                sessions.append(manager._sessions[result.session_id])
            assert len(manager._sessions) == 64
            assert sessions[0].id not in manager._sessions
            assert sessions[0].process.returncode is not None
            with pytest.raises(ValueError, match="unknown or completed"):
                await manager.write_stdin(sessions[0].id, "", yield_seconds=0)
            assert all(s.id in manager._sessions for s in sessions[-8:])
        finally:
            await manager.terminate_all()
        assert all(s.process.returncode is not None for s in sessions)

    asyncio.run(scenario())


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("explicit_cap", [False, True])
@pytest.mark.parametrize("preload", [False, True])
def test_runtime_lifetime_across_turns_and_explicit_timeout(
    tmp_path, nested, explicit_cap, preload
):
    async def scenario():
        requests, effects = [], []
        session_id = None

        class Model:
            async def stream(self, request):
                nonlocal session_id
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1 or (len(requests) == 3 and not explicit_cap):
                    if len(requests) == 1:
                        name, args = (
                            "exec_command",
                            {
                                "cmd": "printf READY; read line; printf FINISHED",
                                "login": False,
                                "tty": True,
                                "yield_time_ms": 0,
                            },
                        )
                    else:
                        name, args = (
                            "write_stdin",
                            {
                                "session_id": session_id,
                                "chars": "finish\n",
                                "yield_time_ms": 0,
                            },
                        )
                    call = (
                        ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments=f"text(await tools.{name}({json.dumps(args)}));",
                        )
                        if nested
                        else ToolCall(new_tool_call_id(), name, args)
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    if explicit_cap:
                        assert "timed out" in result.content
                        # Nested shell timeout preserves the typed shell value; direct
                        # projection additionally carries the ordinary timeout error bit.
                        if not nested:
                            assert result.is_error
                    elif len(requests) == 2:
                        assert "READY" in result.content and not result.is_error
                        session_id = re.search(
                            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", result.content
                        )[0]
                    else:
                        assert "FINISHED" in result.content and not result.is_error
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        settings = CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            command_timeout_seconds=0.05 if explicit_cap else None,
            tool_mode="code_mode" if nested else "direct",
        )

        def create(thread=None):
            runtime = LangGraphRuntime.create(
                settings=settings,
                database_path=tmp_path / "lifetime.db",
                model=Model(),
                thread_id=thread,
            )
            manager = runtime._process_manager
            start = manager._start_session
            enqueue = manager._enqueue_pty_input

            async def counted_start(*args, **kwargs):
                effects.append("spawn")
                return await start(*args, **kwargs)

            async def counted_enqueue(*args):
                effects.append("input")
                return await enqueue(*args)

            manager._start_session = counted_start
            manager._enqueue_pty_input = counted_enqueue
            return runtime

        runtime = create()
        seeded = []
        try:
            if preload:
                for _ in range(64):
                    result = await runtime._process_manager.execute(
                        "exec sleep 20", cwd=tmp_path, login=False, yield_seconds=0
                    )
                    seeded.append(runtime._process_manager._sessions[result.session_id])
                assert effects == ["spawn"] * 64
                effects.clear()
            events = [e async for e in runtime.stream("start")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            if preload:
                # A 50ms timeout finishes inside the 150ms sandbox startup
                # check. No live process is published, so no slot is evicted.
                assert (seeded[0].id in runtime._process_manager._sessions) is explicit_cap
                assert (seeded[0].process.returncode is None) is explicit_cap
            if not explicit_cap:
                session = runtime._process_manager._sessions[session_id]
                assert session.timeout_task is None and session.process.returncode is None
                events = [e async for e in runtime.stream("finish")]
                assert isinstance(events[-1], TurnCompleted), events[-1]
                assert session.process.returncode == 0
            assert len(runtime._process_manager._sessions) == (
                (64 if explicit_cap else 63) if preload else 0
            )
        finally:
            await runtime.aclose()
        assert all(session.process.returncode is not None for session in seeded)
        cold = create(runtime.thread_id)
        try:
            events = [e async for e in cold.stream("continue")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert effects == (["spawn"] if explicit_cap else ["spawn", "input"])
            assert len(requests) == (3 if explicit_cap else 5)
        finally:
            await cold.aclose()

    asyncio.run(scenario())


def test_generated_config_has_no_implicit_cap_and_old_explicit_config_is_retained(tmp_path):
    paths = CorkiPaths.from_home(tmp_path / "corki-home")
    paths.ensure_exists()
    settings = CorkiSettings.for_directory(tmp_path, config_file=paths.config_file)
    assert settings.command_timeout_seconds is None
    old = tmp_path / "old.toml"
    old.write_text("[tools]\ncommand_timeout_seconds = 120\n")
    assert CorkiSettings.for_directory(tmp_path, config_file=old).command_timeout_seconds == 120


def test_failed_eviction_reclaims_new_spawn_before_reporting(tmp_path, monkeypatch):
    async def scenario():
        manager = ProcessManager()
        old = [
            _ProcessSession(str(i), SimpleNamespace(returncode=0), 100, last_used=i)
            for i in range(64)
        ]
        manager._sessions.update((session.id, session) for session in old)
        original = manager._terminate
        new = []

        async def terminate(process):
            if process is old[0].process:
                raise OSError("eviction termination failed")
            if not isinstance(process, SimpleNamespace):
                new.append(process)
                await original(process)

        monkeypatch.setattr(manager, "_terminate", terminate)
        try:
            with pytest.raises(OSError, match="eviction termination failed"):
                await manager.execute("exec sleep 20", cwd=tmp_path, login=False, yield_seconds=0)
            assert len(new) == 1 and new[0].returncode is not None
            assert len(manager._sessions) == 63 and not manager._starting
        finally:
            await manager.terminate_all()

    asyncio.run(scenario())
