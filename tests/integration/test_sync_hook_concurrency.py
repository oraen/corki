"""Synchronous Stop handlers execute together but publish in configuration order."""

import asyncio
import json
import os
import shlex
import sqlite3
import sys

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import HookCompleted, TurnCompleted, TurnFailed
from corki.protocol.ids import ThreadId
from corki.protocol.items import AssistantMessageItem, ContextItem, new_step_id
from corki.protocol.session_source import (
    DEFAULT_SESSION_SOURCE,
    SessionSource,
    SubAgentSource,
    ThreadSpawnSource,
)


@pytest.mark.parametrize("event_name", ["Stop", "SubagentStop"])
def test_sync_commands_overlap_and_publish_in_configuration_order(tmp_path, event_name):
    async def scenario():
        source = tmp_path / "config.toml"
        label = "stop" if event_name == "Stop" else "subagent_stop"
        definition = f"[[hooks.{event_name}]]\n"
        approvals = ""
        for index in range(2):
            script = (
                "import json,sys,time; from pathlib import Path; p=json.load(sys.stdin); "
                f"index={index}\n"
                "if not p['stop_hook_active']:\n"
                " if index == 0:\n"
                "  while not Path('second-finished').exists(): time.sleep(0.01)\n"
                " Path('completion-order').open('a').write(str(index))\n"
                " if index == 1: Path('second-finished').touch()\n"
                "print(json.dumps({} if p['stop_hook_active'] else "
                "{'decision':'block','reason':'VERIFY_'+str(index)}))"
            )
            command = shlex.join([sys.executable, "-c", script])
            fingerprint, _ = command_identity(
                {"type": "command", "command": command, "timeout": 2}, event_name=event_name
            )
            definition += (
                f"[[hooks.{event_name}.hooks]]\ntype='command'\ntimeout=2\n"
                f"command={json.dumps(command)}\n"
            )
            key = f"{source}:{label}:0:{index}"
            approvals += (
                f"[hooks.state.{json.dumps(key)}]\ntrusted_hash={json.dumps(fingerprint)}\n"
            )
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                assert len(requests) <= 2
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                configuration=LocalConfigState(
                    (ConfigLayer(source, "user", contents=definition + approvals),)
                ),
            ),
            model=Model(),
            database_path=tmp_path / "session.db",
            home_path=tmp_path / "home",
            session_source=(
                DEFAULT_SESSION_SOURCE
                if event_name == "Stop"
                else SessionSource.subagent(
                    SubAgentSource("thread_spawn", ThreadSpawnSource(ThreadId("parent"), 1))
                )
            ),
        )
        try:
            events = [event async for event in runtime.stream("finish")]
            assert isinstance(events[-1], TurnCompleted)
            assert (tmp_path / "completion-order").read_text() == "10"
            assert len(requests) == 2
            assert [
                item.content
                for item in requests[1].items
                if isinstance(item, ContextItem) and item.content_kind == f"hook.{label}.feedback"
            ] == ["VERIFY_0", "VERIFY_1"]
            completed = [event.run for event in events if isinstance(event, HookCompleted)]
            assert [run.status for run in completed] == [
                "blocked",
                "blocked",
                "completed",
                "completed",
            ]
            assert [run.key.rsplit(":", 1)[-1] for run in completed] == ["0", "1", "0", "1"]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["close", "journal"])
def test_sync_batch_joins_sibling_processes_on_failure(tmp_path, monkeypatch, failure):
    async def scenario():
        source = tmp_path / "config.toml"
        definition = "[[hooks.Stop]]\n"
        approvals = ""
        for index in range(2):
            script = (
                "import json,os,sys,time; from pathlib import Path; json.load(sys.stdin); "
                f"index={index}; "
                "Path('started-'+str(index)).write_text(str(os.getpid()))\n"
                "while not Path('release-'+str(index)).exists(): time.sleep(0.01)\n"
                "print('{}')"
            )
            command = shlex.join([sys.executable, "-c", script])
            definition += f"[[hooks.Stop.hooks]]\ntype='command'\ncommand={json.dumps(command)}\n"
            fingerprint, _ = command_identity({"type": "command", "command": command})
            approvals += (
                f"[hooks.state.{json.dumps(f'{source}:stop:0:{index}')} ]\n"
                f"trusted_hash={json.dumps(fingerprint)}\n"
            )

        class Model:
            closed = 0
            requests = 0

            async def stream(self, request):
                self.requests += 1
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                self.closed += 1

        model = Model()
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                configuration=LocalConfigState(
                    (ConfigLayer(source, "user", contents=definition + approvals),)
                ),
            ),
            model=model,
            database_path=tmp_path / "session.db",
            home_path=tmp_path / "home",
        )
        if failure == "journal":

            async def fail_commit(*args, **kwargs):
                raise OSError("SYNC_JOURNAL_FAILED")

            monkeypatch.setattr(runtime._repository, "complete_hook_execution", fail_commit)

        async def consume():
            return [event async for event in runtime.stream("finish")]

        task = asyncio.create_task(consume())
        try:
            async with asyncio.timeout(5):
                while len(list(tmp_path.glob("started-*"))) < 2:
                    await asyncio.sleep(0.01)
            pids = [int(path.read_text()) for path in tmp_path.glob("started-*")]
            if failure == "journal":
                (tmp_path / "release-0").touch()
                events = await asyncio.wait_for(task, 5)
                assert isinstance(events[-1], TurnFailed)
                assert not any(isinstance(event, HookCompleted) for event in events)
                assert [event async for event in runtime.resume_pending()] == []
            else:
                await asyncio.wait_for(runtime.aclose(), 5)
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
            for pid in pids:
                with pytest.raises(ProcessLookupError):
                    os.kill(pid, 0)
            with sqlite3.connect(tmp_path / "session.db") as connection:
                rows = connection.execute("SELECT result_json FROM hook_executions").fetchall()
            assert rows == [(None,), (None,)]
            assert model.requests == 1
            assert len(list(tmp_path.glob("started-*"))) == 2
        finally:
            await runtime.aclose()
            await asyncio.gather(task, return_exceptions=True)
        assert model.closed == 1

    asyncio.run(scenario())
