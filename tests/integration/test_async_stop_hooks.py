"""Asynchronous hooks are session-owned effects, never late Turn control."""

import asyncio
import json
import os
import shlex
import sys
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime, stop_hooks
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import (
    HookCompleted,
    HookStarted,
    TurnCompleted,
    TurnFailed,
    WarningEvent,
)
from corki.protocol.ids import ThreadId, new_turn_id
from corki.protocol.items import AssistantMessageItem, ContextItem, UserMessageItem, new_step_id
from corki.protocol.session_source import (
    DEFAULT_SESSION_SOURCE,
    SessionSource,
    SubAgentSource,
    ThreadSpawnSource,
)
from corki.sessions import TurnRecord, TurnStatus


def settings_for(tmp_path, event_name, *, count=1, script=None):
    script = script or (
        "import json,os,sys,time; from pathlib import Path; json.load(sys.stdin); "
        "Path('started-'+str(os.getpid())).write_text(str(os.getpid())); "
        "exec(\"while not Path('release').exists(): time.sleep(0.01)\"); "
        "print(json.dumps({'continue':False,'stopReason':'DO_NOT_CONTROL',"
        "'decision':'block','reason':'DO_NOT_INJECT','systemMessage':'ASYNC_WARNING'}))"
    )
    command = shlex.join([sys.executable, "-c", script])
    handler = {"type": "command", "command": command, "async": True}
    fingerprint, _ = command_identity(handler, event_name=event_name)
    source = tmp_path / "config.toml"
    label = "stop" if event_name == "Stop" else "subagent_stop"
    definition = f"[[hooks.{event_name}]]\n"
    for _ in range(count):
        definition += (
            f"[[hooks.{event_name}.hooks]]\ntype='command'\nasync=true\n"
            f"command={json.dumps(command)}\n"
        )
    for index in range(count):
        key = f"{source}:{label}:0:{index}"
        definition += f"[hooks.state.{json.dumps(key)}]\ntrusted_hash={json.dumps(fingerprint)}\n"
    return CorkiSettings(
        tmp_path,
        skills_enabled=False,
        plugins_enabled=False,
        configuration=LocalConfigState((ConfigLayer(source, "user", contents=definition),)),
    )


class Model:
    def __init__(self):
        self.requests = []
        self.closed = 0

    async def stream(self, request):
        self.requests.append(request)
        assert not any(
            isinstance(item, ContextItem) and item.content in {"DO_NOT_CONTROL", "DO_NOT_INJECT"}
            for item in request.items
        )
        yield ModelCompleted(
            (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
        )

    async def aclose(self):
        self.closed += 1


@pytest.mark.parametrize("manual_compact", [False, True])
@pytest.mark.parametrize("disable", ["remove", "feature"])
def test_open_hook_transcript_tracks_later_turn_after_hook_removal(
    tmp_path, manual_compact, disable
):
    async def scenario():
        script = (
            "import json,sys,time; from pathlib import Path; p=json.load(sys.stdin); "
            "f=open(p['transcript_path']); Path('before.jsonl').write_text(f.read()); "
            "Path('reader-ready').touch(); "
            "exec(\"while not Path('release').exists(): time.sleep(0.01)\"); "
            "Path('after.jsonl').write_text(f.read()); f.close(); print('{}')"
        )
        model = Model()
        runtime = await LangGraphRuntime.acreate(
            settings=settings_for(tmp_path, "Stop", script=script),
            model=model,
            database_path=tmp_path / "session.db",
            home_path=tmp_path / "home",
        )
        try:
            first = [event async for event in runtime.stream("FIRST_RAW_INPUT")]
            assert isinstance(first[-1], TurnCompleted)
            async with asyncio.timeout(5):
                while not (tmp_path / "reader-ready").exists():
                    await asyncio.sleep(0.01)
            owner = runtime._graph._stop_hooks._async
            tasks = dict(owner._tasks)
            assert len(tasks) == 1
            publish = await runtime._prepare_configuration_reload(
                runtime._settings.configuration if disable == "feature" else LocalConfigState(()),
                {"features": {"hooks": False}} if disable == "feature" else {},
            )
            publish()
            assert runtime._graph._stop_hooks._async is owner
            assert owner._tasks == tasks
            if manual_compact:
                compacted = [event async for event in runtime.compact()]
                assert isinstance(compacted[-1], TurnCompleted)
            second = [event async for event in runtime.stream("SECOND_RAW_INPUT")]
            assert isinstance(second[-1], TurnCompleted)
            assert owner._tasks == tasks
            (tmp_path / "release").touch()
            async with asyncio.timeout(5):
                while owner._tasks:
                    await asyncio.sleep(0.01)
            before = [
                json.loads(line) for line in (tmp_path / "before.jsonl").read_text().splitlines()
            ]
            after = [
                json.loads(line) for line in (tmp_path / "after.jsonl").read_text().splitlines()
            ]
            assert any(row.get("payload", {}).get("content") == "FIRST_RAW_INPUT" for row in before)
            assert not any(
                row.get("payload", {}).get("content") == "SECOND_RAW_INPUT" for row in before
            )
            assert any(row.get("payload", {}).get("content") == "SECOND_RAW_INPUT" for row in after)
            path = await runtime._repository.materialize_transcript(runtime.thread_id)
            complete = [json.loads(line) for line in path.read_text().splitlines()]
            assert complete[: len(before) + len(after)] == before + after
            assert any(
                row.get("payload", {}).get("content") == "FIRST_RAW_INPUT" for row in complete
            )
            assert len(model.requests) == (3 if manual_compact else 2)
            with runtime._repository._connect() as connection:
                records = connection.execute("SELECT result_json FROM hook_executions").fetchall()
            assert len(records) == 1 and records[0][0] is not None
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("event_name", ["Stop", "SubagentStop"])
@pytest.mark.parametrize("manual_compact", [False, True])
def test_async_hook_finishes_turn_before_command_and_warns_at_next_boundary(
    tmp_path, event_name, manual_compact
):
    async def scenario():
        model = Model()
        source = (
            DEFAULT_SESSION_SOURCE
            if event_name == "Stop"
            else SessionSource.subagent(
                SubAgentSource("thread_spawn", ThreadSpawnSource(ThreadId("parent"), 1))
            )
        )
        runtime = await LangGraphRuntime.acreate(
            settings=settings_for(tmp_path, event_name),
            model=model,
            database_path=tmp_path / "session.db",
            home_path=tmp_path / "home",
            session_source=source,
        )
        try:
            async with asyncio.timeout(5):
                events = [event async for event in runtime.stream("first")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(model.requests) == 1
            assert not any(isinstance(event, (HookStarted, HookCompleted)) for event in events)
            async with asyncio.timeout(5):
                while not list(tmp_path.glob("started-*")):
                    await asyncio.sleep(0.01)
            assert not (tmp_path / "release").exists()
            # Reload must not cancel an admitted command or replace its owner.
            owner = runtime._graph._stop_hooks._async
            publish = await runtime._prepare_configuration_reload(LocalConfigState(()), {})
            publish()
            assert runtime._graph._stop_hooks._async is owner
            (tmp_path / "release").touch()
            async with asyncio.timeout(5):
                while owner._tasks:
                    await asyncio.sleep(0.01)
            if manual_compact:
                compacted = [event async for event in runtime.compact()]
                assert isinstance(compacted[-1], TurnCompleted)
                assert not any(
                    isinstance(event, WarningEvent) and event.message == "ASYNC_WARNING"
                    for event in compacted
                )
                assert len(owner._results) == 1
            second = [event async for event in runtime.stream("second")]
            assert isinstance(second[-1], TurnCompleted)
            assert len(model.requests) == (3 if manual_compact else 2)
            assert any(
                isinstance(event, WarningEvent) and event.message == "ASYNC_WARNING"
                for event in second
            )
            assert not any(isinstance(event, (HookStarted, HookCompleted)) for event in second)
            assert len(list(tmp_path.glob("started-*"))) == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_later_async_command_is_started_before_earlier_sync_command_finishes(tmp_path):
    async def scenario():
        settings = settings_for(tmp_path, "Stop")
        layer = settings.configuration.layers[0]
        script = (
            "import time; from pathlib import Path; "
            "exec(\"while not list(Path('.').glob('started-*')): time.sleep(0.01)\"); "
            "Path('sync-finished').touch(); print('{}')"
        )
        command = shlex.join([sys.executable, "-c", script])
        fingerprint, _ = command_identity({"type": "command", "command": command})
        sync = f"[[hooks.Stop.hooks]]\ntype='command'\ncommand={json.dumps(command)}\n"
        key = f"{layer.file}:stop:0:0"
        contents = layer.contents.replace(
            json.dumps(key), json.dumps(f"{layer.file}:stop:0:1")
        ).replace("[[hooks.Stop.hooks]]", sync + "[[hooks.Stop.hooks]]", 1)
        contents += f"[hooks.state.{json.dumps(key)}]\ntrusted_hash={json.dumps(fingerprint)}\n"
        settings = replace(
            settings, configuration=LocalConfigState((replace(layer, contents=contents),))
        )
        model = Model()
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            model=model,
            database_path=tmp_path / "session.db",
            home_path=tmp_path / "home",
        )
        try:
            async with asyncio.timeout(5):
                events = [event async for event in runtime.stream("mixed hooks")]
            assert isinstance(events[-1], TurnCompleted)
            assert (tmp_path / "sync-finished").exists()
            assert not (tmp_path / "release").exists()
            assert len(model.requests) == 1
            assert len(runtime._graph._stop_hooks._async._tasks) == 1
            starts = [event for event in events if isinstance(event, HookStarted)]
            completions = [event for event in events if isinstance(event, HookCompleted)]
            assert len(starts) == len(completions) == 1
            assert starts[0].run.key == completions[0].run.key == key
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("close_before_release", [False, True])
def test_async_hook_concurrency_and_owned_shutdown(tmp_path, close_before_release):
    async def scenario():
        runtime = await LangGraphRuntime.acreate(
            settings=settings_for(tmp_path, "Stop", count=9),
            model=Model(),
            database_path=tmp_path / "session.db",
            home_path=tmp_path / "home",
        )
        owner = runtime._graph._stop_hooks._async
        try:
            async with asyncio.timeout(5):
                events = [event async for event in runtime.stream("first")]
            assert isinstance(events[-1], TurnCompleted)
            async with asyncio.timeout(5):
                while len(list(tmp_path.glob("started-*"))) < 8:
                    await asyncio.sleep(0.01)
            assert len(list(tmp_path.glob("started-*"))) == 8
            assert len(owner._tasks) == 9
            pids = [int(path.read_text()) for path in tmp_path.glob("started-*")]
            if close_before_release:
                async with asyncio.timeout(5):
                    await runtime.aclose()
                assert not owner._tasks
                assert not owner._results
                assert len(list(tmp_path.glob("started-*"))) == 8
                for pid in pids:
                    with pytest.raises(ProcessLookupError):
                        os.kill(pid, 0)
            else:
                (tmp_path / "release").touch()
                async with asyncio.timeout(5):
                    while owner._tasks:
                        await asyncio.sleep(0.01)
                assert len(list(tmp_path.glob("started-*"))) == 9
                assert len(owner._results) == 9
            with runtime._repository._connect() as connection:
                records = connection.execute("SELECT result_json FROM hook_executions").fetchall()
            assert len(records) == 9
            assert all((record[0] is None) == close_before_release for record in records)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize("event_name", ["Stop", "SubagentStop"])
def test_async_hook_cold_recovery_does_not_repeat_effect(tmp_path, completed, event_name):
    async def scenario():
        class Sink:
            async def emit(self, event):
                pass

        source = (
            DEFAULT_SESSION_SOURCE
            if event_name == "Stop"
            else SessionSource.subagent(
                SubAgentSource("thread_spawn", ThreadSpawnSource(ThreadId("parent"), 1))
            )
        )
        settings = settings_for(tmp_path, event_name)
        model = Model()
        database = tmp_path / "session.db"
        warm = await LangGraphRuntime.acreate(
            settings=settings,
            model=model,
            database_path=database,
            home_path=tmp_path / "home",
            session_source=source,
        )
        try:
            await warm._ensure_ready()
            thread, turn = warm.thread_id, new_turn_id()
            user = UserMessageItem("first", turn)
            await warm._repository.save_turn(
                TurnRecord(turn, thread, TurnStatus.RUNNING, user.content)
            )
            await warm._repository.append_items(thread, (user,))
            admitted = asyncio.Event()
            original = warm._graph._stop_hooks.run

            async def held(*args, **kwargs):
                result = await original(*args, **kwargs)
                admitted.set()
                await asyncio.Future()  # Before finalize node checkpoint/Turn terminal.
                return result

            warm._graph._stop_hooks.run = held
            invocation = asyncio.create_task(
                warm._compiled.ainvoke(
                    _initial_state(thread, turn, settings, user),
                    context=GraphRunContext(events=Sink(), session_source=source),
                    config=warm._graph_config(turn),
                    durability="sync",
                )
            )
            try:
                async with asyncio.timeout(5):
                    await admitted.wait()
                    while not list(tmp_path.glob("started-*")):
                        await asyncio.sleep(0.01)
                    if completed:
                        (tmp_path / "release").touch()
                        while warm._graph._stop_hooks._async._tasks:
                            await asyncio.sleep(0.01)
            finally:
                invocation.cancel()
                await asyncio.gather(invocation, return_exceptions=True)
            before = await warm._repository.load_items(thread)
            assert len(model.requests) == 1
        finally:
            await warm.aclose()
        cold_model = Model()
        cold = await LangGraphRuntime.acreate(
            settings=settings,
            model=cold_model,
            database_path=database,
            home_path=tmp_path / "home",
            session_source=source,
            thread_id=thread,
        )
        try:
            events = [event async for event in cold.resume_pending()]
            assert isinstance(events[-1], TurnCompleted if completed else TurnFailed)
            if not completed:
                assert "unknown" in events[-1].error
            assert not cold_model.requests
            assert len(list(tmp_path.glob("started-*"))) == 1
            assert not any(isinstance(event, (HookStarted, HookCompleted)) for event in events)
            history = await cold._repository.load_items(thread)
            assert history[: len(before)] == before
            assert sum(isinstance(item, UserMessageItem) for item in history) == 1
            assert not any(
                isinstance(item, ContextItem)
                and item.content in {"DO_NOT_INJECT", "DO_NOT_CONTROL"}
                for item in history
            )
            assert [event async for event in cold.resume_pending()] == []
        finally:
            await cold.aclose()

    asyncio.run(scenario())


def test_cancelled_close_waiter_does_not_abandon_async_hook_cleanup(tmp_path, monkeypatch):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        execute = stop_hooks.run_command

        async def held_cleanup(*args, **kwargs):
            try:
                return await execute(*args, **kwargs)
            finally:
                entered.set()
                await release.wait()

        monkeypatch.setattr(stop_hooks, "run_command", held_cleanup)
        model = Model()
        runtime = await LangGraphRuntime.acreate(
            settings=settings_for(tmp_path, "Stop"),
            model=model,
            database_path=tmp_path / "session.db",
            home_path=tmp_path / "home",
        )
        try:
            assert isinstance([event async for event in runtime.stream("first")][-1], TurnCompleted)
            async with asyncio.timeout(5):
                while not list(tmp_path.glob("started-*")):
                    await asyncio.sleep(0.01)
            closer = asyncio.create_task(runtime.aclose())
            async with asyncio.timeout(5):
                await entered.wait()
            closer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closer
            assert model.closed == 0
            assert runtime._graph._stop_hooks._async._tasks
            release.set()
            async with asyncio.timeout(5):
                await runtime.aclose()
            assert model.closed == 1
            assert not runtime._graph._stop_hooks._async._tasks
            assert not runtime._graph._stop_hooks._async._results
        finally:
            release.set()
            await runtime.aclose()

    asyncio.run(scenario())


def test_background_journal_failure_is_isolated_and_never_retries_effect(
    tmp_path, monkeypatch, caplog
):
    async def scenario():
        model = Model()
        runtime = await LangGraphRuntime.acreate(
            settings=settings_for(tmp_path, "Stop"),
            model=model,
            database_path=tmp_path / "session.db",
            home_path=tmp_path / "home",
        )
        try:

            async def fail_commit(*args, **kwargs):
                raise OSError("ASYNC_JOURNAL_FAILED")

            monkeypatch.setattr(runtime._repository, "complete_hook_execution", fail_commit)
            events = [event async for event in runtime.stream("first")]
            assert isinstance(events[-1], TurnCompleted)
            async with asyncio.timeout(5):
                while not list(tmp_path.glob("started-*")):
                    await asyncio.sleep(0.01)
            publish = await runtime._prepare_configuration_reload(LocalConfigState(()), {})
            publish()
            (tmp_path / "release").touch()
            owner = runtime._graph._stop_hooks._async
            async with asyncio.timeout(5):
                while owner._tasks:
                    await asyncio.sleep(0.01)
            assert not owner._results
            assert "ASYNC_JOURNAL_FAILED" in caplog.text
            second = [event async for event in runtime.stream("second")]
            assert isinstance(second[-1], TurnCompleted)
            assert len(model.requests) == 2
            assert len(list(tmp_path.glob("started-*"))) == 1
            with runtime._repository._connect() as connection:
                rows = connection.execute("SELECT result_json FROM hook_executions").fetchall()
            assert len(rows) == 1 and rows[0][0] is None
            assert [event async for event in runtime.resume_pending()] == []
        finally:
            await runtime.aclose()
        assert model.closed == 1

    asyncio.run(scenario())
