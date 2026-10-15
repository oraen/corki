"""Shutdown ledger failures remain failures without replaying shutdown effects."""

import asyncio
import json
import sqlite3

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import HookCompleted, TurnCompleted
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    "method", ["save_hook_batch", "claim_hook_execution", "complete_hook_execution"]
)
@pytest.mark.parametrize("after", [False, True])
def test_session_end_commit_failure_closes_without_replay(tmp_path, monkeypatch, method, after):
    import corki.core.session_end_hooks as end

    async def scenario():
        path = tmp_path / "config.toml"
        fingerprint, _ = command_identity(
            {"type": "command", "command": "shutdown"}, event_name="SessionEnd"
        )
        document = (
            "[[hooks.SessionEnd]]\n[[hooks.SessionEnd.hooks]]\n"
            'type="command"\ncommand="shutdown"\n'
            f"[hooks.state.{json.dumps(f'{path}:session_end:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        effects, closes = [], []

        class Model:
            async def stream(self, request):
                yield ModelCompleted(())

            async def aclose(self):
                closes.append("model")

        database = tmp_path / "state.db"
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                configuration=LocalConfigState((ConfigLayer(path, "user", contents=document),)),
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=database,
            home_path=tmp_path,
        )
        events = [e async for e in runtime.stream("INPUT")]
        assert isinstance(events[-1], TurnCompleted)
        original = getattr(runtime._repository, method)
        close_repository = runtime._repository.close
        fault = OSError("shutdown ledger boundary")

        async def failing(*args, **kwargs):
            if after:
                await original(*args, **kwargs)
            raise fault

        async def run(*args, **kwargs):
            effects.append("effect")
            return {"exit_code": 0, "stdout": "", "stderr": ""}

        async def close():
            closes.append("repository")
            await close_repository()

        monkeypatch.setattr(end, "run_command", run)
        monkeypatch.setattr(runtime._repository, method, failing)
        monkeypatch.setattr(runtime._repository, "close", close)
        notifications = []
        for attempt in range(2):
            with pytest.raises(OSError) as caught:
                async for event in runtime.stream_close():
                    if attempt == 0:
                        notifications.append(event)
            assert caught.value is fault
            assert runtime._close_task.done() and not runtime._close_storage_pending
            assert not runtime._writer.held and runtime._checkpointer is None
        assert closes == ["model", "repository"]
        assert effects == (["effect"] if method == "complete_hook_execution" else [])
        completed = [e for e in notifications if isinstance(e, HookCompleted)]
        assert len(completed) == (0 if method == "save_hook_batch" else 1)
        assert all(e.run.status == "failed" for e in completed)
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT status FROM turns").fetchall() == [("completed",)]
            results = connection.execute("SELECT result_json FROM hook_executions").fetchall()
        if method == "complete_hook_execution":
            assert len(results) == 1
            assert (results[0][0] is not None) == after
        elif method == "claim_hook_execution" and after:
            assert results == [(None,)]
        else:
            assert results == []

    asyncio.run(scenario())
