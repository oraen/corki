import asyncio
import json
import os
import shlex
import sys

import pytest

from corki import __version__
from corki.code_mode.service import CodeModeService
from corki.config import CorkiSettings, ShellEnvironmentPolicy
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall

_NAMES = [
    "CODEX_THREAD_ID",
    "CODEX_SESSION_ID",
    "CODEX_VERSION",
    "CODEX_PERMISSION_PROFILE",
    "CODEX_APPLY_PATCH_PRESERVE_LINE_ENDINGS",
    "codex_apply_patch_preserve_line_endings",
]


def _arguments(tty):
    probe = (
        "import os,json; print('IDENTITY='+json.dumps([os.getenv(k) for k in "
        + repr(_NAMES)
        + "]))"
    )
    return {
        "cmd": shlex.join([sys.executable, "-I", "-S", "-c", probe]),
        "login": False,
        "tty": tty,
        "yield_time_ms": 1000,
    }


def _identity(request):
    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
    assert not result.is_error, result.content
    line = next(line for line in result.content.splitlines() if line.startswith("IDENTITY="))
    return json.loads(line.removeprefix("IDENTITY="))


class ProbeModel:
    def __init__(self, mode="direct", tty=False):
        self.requests, self.mode, self.tty = [], mode, tty

    async def stream(self, request):
        self.requests.append(request)
        turn, step = request.items[-1].turn_id, new_step_id()
        if len(self.requests) % 2:
            arguments = _arguments(self.tty)
            call = (
                ToolCall(new_tool_call_id(), "exec_command", arguments)
                if self.mode == "direct"
                else ToolCall(
                    new_tool_call_id(),
                    "exec",
                    None,
                    input_kind="freeform",
                    raw_arguments="text((await tools.exec_command("
                    + json.dumps(arguments)
                    + ")).output)",
                )
            )
            item = ToolCallItem(call, turn, step)
        else:
            item = AssistantMessageItem("done", turn, step)
        yield ModelCompleted((item,))

    async def aclose(self):
        pass


@pytest.mark.skipif(os.name == "nt", reason="POSIX probe quoting and PTY")
@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("tty", [False, True])
def test_runtime_injects_own_identity_across_turns_and_reopen(tmp_path, monkeypatch, mode, tty):
    if mode != "direct" and not CodeModeService.available():
        pytest.skip("install corki[code-mode]")
    for name in _NAMES:
        monkeypatch.setenv(name, "fake-parent")
    settings = CorkiSettings(
        working_directory=tmp_path,
        skills_enabled=False,
        tool_mode=mode,
        shell_environment_policy=ShellEnvironmentPolicy(
            inherit="none",
            set={name: "fake-configured" for name in _NAMES},
        ),
    )

    async def scenario():
        def create(thread=None):
            return LangGraphRuntime.create(
                settings=settings,
                database_path=tmp_path / "sessions.db",
                model=ProbeModel(mode, tty),
                thread_id=thread,
            )

        runtime = create()
        thread = runtime.thread_id
        expected = [str(thread), str(thread), __version__, None, None, None]
        try:
            for prompt in ("first", "second"):
                events = [e async for e in runtime.stream(prompt)]
                assert isinstance(events[-1], TurnCompleted), events[-1]
                assert _identity(runtime._model.requests[-1]) == expected
            await runtime.aclose()
            runtime = create(thread)
            events = [e async for e in runtime.stream("cold reopen")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert _identity(runtime._model.requests[-1]) == expected
            await runtime.aclose()
            runtime = create()
            events = [e async for e in runtime.stream("new root")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert _identity(runtime._model.requests[-1]) == [
                str(runtime.thread_id),
                str(runtime.thread_id),
                __version__,
                None,
                None,
                None,
            ]
            assert runtime.thread_id != thread
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.skipif(os.name == "nt", reason="POSIX probe quoting")
@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_stored_custom_session_identity_wins_on_cold_reopen(tmp_path, mode):
    from corki.protocol.ids import new_session_id

    if mode != "direct" and not CodeModeService.available():
        pytest.skip("install corki[code-mode]")

    async def scenario():
        settings = CorkiSettings(working_directory=tmp_path, skills_enabled=False, tool_mode=mode)
        stored = new_session_id()
        runtime = LangGraphRuntime.create(
            settings=settings,
            database_path=tmp_path / "session.db",
            model=ProbeModel(mode),
            session_id=stored,
        )
        try:
            with pytest.raises(RuntimeError, match="before initialization"):
                _ = runtime.session_id
            events = [e async for e in runtime.stream("original internal")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            thread = runtime.thread_id
            assert runtime.session_id == stored
            assert _identity(runtime._model.requests[-1])[:2] == [thread, stored]
            await runtime.aclose()
            for supplied in (None, new_session_id()):
                runtime = LangGraphRuntime.create(
                    settings=settings,
                    database_path=tmp_path / "session.db",
                    model=ProbeModel(mode),
                    thread_id=thread,
                    session_id=supplied,
                )
                events = [e async for e in runtime.stream("cold internal")]
                assert isinstance(events[-1], TurnCompleted), events[-1]
                assert runtime.session_id == stored
                assert _identity(runtime._model.requests[-1])[:2] == [thread, stored]
                await runtime.aclose()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.skipif(os.name == "nt", reason="POSIX probe quoting")
def test_memory_consolidation_has_fresh_independent_internal_identity(tmp_path, monkeypatch):
    for name in _NAMES:
        monkeypatch.setenv(name, "fake-outer")

    async def scenario():
        class Main:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("main", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Memory(ProbeModel):
            async def stream(self, request):
                if not self.requests:
                    async for event in super().stream(request):
                        yield event
                else:
                    self.requests.append(request)
                    yield ModelCompleted(
                        (
                            AssistantMessageItem(
                                json.dumps(
                                    {
                                        "memory": "verified",
                                        "memory_summary": "verified",
                                        "skills": [],
                                    }
                                ),
                                request.items[-1].turn_id,
                                new_step_id(),
                            ),
                        )
                    )

        memory = Memory()
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                memories_enabled=True,
                memories_consolidation_model="fixture",
            ),
            database_path=tmp_path / "parent.db",
            memory_root=tmp_path / "memories",
            model=Main(),
            memory_model=memory,
        )
        try:
            events = [e async for e in runtime.stream("parent task")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            report = await runtime._memory_service.wait()
            assert report.consolidated and not report.failed, runtime._memory_service.warnings
            child_thread, child_session, *metadata = _identity(memory.requests[-1])
            assert child_thread and child_session and child_thread != child_session
            assert runtime.session_id == runtime.thread_id
            assert runtime.session_id not in (child_thread, child_session)
            assert metadata == [__version__, None, None, None]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("invalid", [None, "", "bad\0id"])
def test_corrupt_stored_identity_stops_runtime_before_model_or_tool_admission(tmp_path, invalid):
    import sqlite3

    from corki.protocol.ids import new_thread_id
    from corki.storage import SQLiteSessionRepository, StorageIntegrityError

    async def scenario():
        path, thread = tmp_path / "corrupt.db", new_thread_id()
        repository = SQLiteSessionRepository(path)
        await repository.create_thread(thread, tmp_path)
        with sqlite3.connect(path) as db:
            db.execute("UPDATE threads SET session_id=?", (invalid,))
        model = ProbeModel()
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=path,
            thread_id=thread,
            model=model,
        )
        try:
            with pytest.raises(StorageIntegrityError, match="invalid stored"):
                _ = [e async for e in runtime.stream("must not run")]
            assert model.requests == []
            assert not runtime._process_manager.list_background_terminals()
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.skipif(os.name == "nt", reason="POSIX probe quoting")
@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
def test_prepared_checkpoint_uses_restored_durable_identity(tmp_path, mode):
    from corki.core.graph import GraphRunContext
    from corki.core.runtime import _initial_state
    from corki.protocol.ids import new_session_id, new_turn_id
    from corki.protocol.items import UserMessageItem
    from corki.sessions import TurnRecord, TurnStatus

    if mode != "direct" and not CodeModeService.available():
        pytest.skip("install corki[code-mode]")

    class Sink:
        async def emit(self, event):
            pass

    async def scenario():
        settings = CorkiSettings(working_directory=tmp_path, skills_enabled=False, tool_mode=mode)
        session = new_session_id()

        def create(thread=None, requested=session):
            return LangGraphRuntime.create(
                settings=settings,
                database_path=tmp_path / "checkpoint.db",
                model=ProbeModel(mode),
                thread_id=thread,
                session_id=requested,
            )

        runtime = create()
        try:
            events = [e async for e in runtime.stream("initialize")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            thread, turn = runtime.thread_id, new_turn_id()
            user = UserMessageItem("prepared request", turn)
            await runtime._repository.save_turn(
                TurnRecord(turn, thread, TurnStatus.RUNNING, user.content)
            )
            config = runtime._graph_config(turn)
            await runtime._compiled.ainvoke(
                _initial_state(thread, turn, settings, user),
                config=config,
                context=GraphRunContext(events=Sink()),
                interrupt_before=["call_model"],
            )
            assert (await runtime._compiled.aget_state(config)).next == ("call_model",)
            prepared = await runtime._repository.load_items(thread)
            await runtime.aclose()
            runtime = create(thread, new_session_id())
            events = [e async for e in runtime.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            visible = tuple(
                i for i in prepared if not isinstance(i, ContextItem) or not i.is_snapshot_only
            )
            expected_items = visible  # No private metadata is added to ordinary replay.
            assert runtime._model.requests[0].items == expected_items
            assert all(
                item.response_item_metadata_json is None
                for item in visible
                if isinstance(item, AssistantMessageItem)
            )
            assert _identity(runtime._model.requests[-1])[:2] == [thread, session]
            assert runtime.session_id == session
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.skipif(os.name == "nt", reason="POSIX probe quoting and PTY")
@pytest.mark.parametrize("tty", [False, True])
def test_host_prewarm_resolves_identity_before_first_stream(tmp_path, tty):
    from corki.protocol.ids import new_session_id

    async def scenario():
        session = new_session_id()
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "prewarm.db",
            model=ProbeModel(),
            session_id=session,
        )
        try:
            probe = await runtime._process_manager.execute(
                _arguments(tty)["cmd"],
                cwd=tmp_path,
                yield_seconds=1,
                login=False,
                tty=tty,
            )
            assert probe.exit_code == 0
            line = next(line for line in probe.output.splitlines() if line.startswith("IDENTITY="))
            assert json.loads(line.removeprefix("IDENTITY="))[:2] == [runtime.thread_id, session]
            assert runtime.session_id == session and runtime._model.requests == []
            events = [e async for e in runtime.stream("after prewarm")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert _identity(runtime._model.requests[-1])[:2] == [runtime.thread_id, session]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
