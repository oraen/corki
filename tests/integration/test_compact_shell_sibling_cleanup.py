"""A compact Hook batch joins every owned shell process before Turn termination."""

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
from corki.protocol.events import HookCompleted, TurnFailed
from corki.protocol.items import AssistantMessageItem, CompactionItem, new_step_id


@pytest.mark.skipif(os.name != "posix", reason="shell Hook process containment requires POSIX")
@pytest.mark.parametrize("event", ["PreCompact", "PostCompact"])
@pytest.mark.parametrize("failure", ["close", "journal"])
def test_compact_shell_batch_joins_siblings_before_terminal(tmp_path, monkeypatch, event, failure):
    async def scenario():
        source = tmp_path / "config.toml"
        event_key = "pre_compact" if event == "PreCompact" else "post_compact"
        document = f"[[hooks.{event}]]\nmatcher='manual'\n"
        for index in range(2):
            script = (
                "import json,os,sys,time; from pathlib import Path; json.load(sys.stdin); "
                f"index={index}; "
                "Path('started-'+str(index)).write_text(str(os.getpid()))\n"
                "while not Path('release-'+str(index)).exists(): time.sleep(0.01)\n"
                "print('{}')"
            )
            command = shlex.join([sys.executable, "-c", script])
            document += f"[[hooks.{event}.hooks]]\ntype='command'\ncommand={json.dumps(command)}\n"
            fingerprint, _ = command_identity(
                {"type": "command", "command": command},
                event_name=event,
                matcher="manual",
            )
            document += (
                f"[hooks.state.{json.dumps(f'{source}:{event_key}:0:{index}')} ]\n"
                f"trusted_hash={json.dumps(fingerprint)}\n"
            )

        class Model:
            requests = 0

            async def stream(self, request):
                self.requests += 1
                yield ModelCompleted(
                    (AssistantMessageItem("summary", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        model = Model()
        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            compact_prompt="SUMMARIZE_FOR_TEST",
            configuration=LocalConfigState((ConfigLayer(source, "user", contents=document),)),
        )
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            model=model,
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )
        thread_id = runtime.thread_id
        if failure == "journal":

            async def fail_commit(*args, **kwargs):
                raise OSError("COMPACT_JOURNAL_FAILED")

            monkeypatch.setattr(runtime._repository, "complete_hook_execution", fail_commit)

        events = []

        async def consume():
            async for item in runtime.compact():
                if isinstance(item, TurnFailed):
                    for path in tmp_path.glob("started-*"):
                        with pytest.raises(ProcessLookupError):
                            os.kill(int(path.read_text()), 0)
                events.append(item)

        task = asyncio.create_task(consume())
        pids = []
        try:
            async with asyncio.timeout(5):
                while len(list(tmp_path.glob("started-*"))) < 2:
                    await asyncio.sleep(0.01)
            pids = [int(path.read_text()) for path in tmp_path.glob("started-*")]
            if failure == "journal":
                (tmp_path / "release-0").touch()
                await asyncio.wait_for(task, 5)
                assert isinstance(events[-1], TurnFailed)
                assert not any(isinstance(item, HookCompleted) for item in events)
            else:
                await asyncio.wait_for(runtime.aclose(), 5)
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
            for pid in pids:
                with pytest.raises(ProcessLookupError):
                    os.kill(pid, 0)
            assert len(list(tmp_path.glob("started-*"))) == 2
            with sqlite3.connect(tmp_path / "state.db") as connection:
                rows = connection.execute("SELECT result_json FROM hook_executions").fetchall()
            assert rows == [(None,), (None,)]
            assert model.requests == (0 if event == "PreCompact" else 1)
            if failure == "journal":
                stored = await runtime._repository.load_items(thread_id)
                assert sum(isinstance(item, CompactionItem) for item in stored) == (
                    event == "PostCompact"
                )
        finally:
            await runtime.aclose()
            await asyncio.gather(task, return_exceptions=True)

        cold = await LangGraphRuntime.acreate(
            settings=settings,
            model=Model(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
            thread_id=thread_id,
        )
        try:
            assert [item async for item in cold.resume_pending()] == []
            assert len(list(tmp_path.glob("started-*"))) == 2
        finally:
            await cold.aclose()

    asyncio.run(scenario())
