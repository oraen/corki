"""Teardown hooks keep configured notification order despite completion order."""

import asyncio
import json

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import HookCompleted, HookStarted, TurnCompleted, WarningEvent
from corki.tools import ToolRegistry


@pytest.mark.parametrize("transcript_fails", [False, True])
def test_session_end_reverse_completion_and_failure_preserve_shutdown(
    tmp_path, monkeypatch, transcript_fails
):
    import corki.core.session_end_hooks as end

    async def scenario():
        path = tmp_path / "config.toml"
        fragments = []
        for index, command in enumerate(("first", "second")):
            fingerprint, _ = command_identity(
                {"type": "command", "command": command}, event_name="SessionEnd"
            )
            fragments.append(
                "[[hooks.SessionEnd]]\n[[hooks.SessionEnd.hooks]]\n"
                f'type="command"\ncommand={json.dumps(command)}\n'
                f"[hooks.state.{json.dumps(f'{path}:session_end:{index}:0')}]\n"
                f"trusted_hash={json.dumps(fingerprint)}\n"
            )
        order = []
        second_finished = asyncio.Event()

        class Model:
            async def stream(self, request):
                yield ModelCompleted(())

            async def aclose(self):
                order.append("model_close")

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                configuration=LocalConfigState(
                    (ConfigLayer(path, "user", contents="".join(fragments)),)
                ),
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )
        repository_close = runtime._repository.close

        async def close():
            order.append("repository_close")
            await repository_close()

        async def run(command, payload, **kwargs):
            assert order[0] == "model_close" and "repository_close" not in order
            assert (payload["transcript_path"] is None) == transcript_fails
            if command.command == "first":
                await asyncio.wait_for(second_finished.wait(), 5)
                order.append("first")
                return {"exit_code": 0, "stdout": '{"continue":false}', "stderr": ""}
            order.append("second")
            second_finished.set()
            raise OSError("second hook failed")

        async def unavailable(*args):
            raise OSError("transcript unavailable")

        monkeypatch.setattr(end, "run_command", run)
        monkeypatch.setattr(runtime._repository, "close", close)
        if transcript_fails:
            monkeypatch.setattr(runtime._repository, "materialize_transcript", unavailable)
        try:
            assert isinstance([e async for e in runtime.stream("INPUT")][-1], TurnCompleted)
            events = [e async for e in runtime.stream_close()]
            assert order == ["model_close", "second", "first", "repository_close"]
            started = [e for e in events if isinstance(e, HookStarted)]
            completed = [e for e in events if isinstance(e, HookCompleted)]
            assert len(started) == len(completed) == 2
            assert [e.run.id for e in started] == [e.run.id for e in completed]
            assert [e.run.status for e in completed] == ["completed", "failed"]
            assert any(isinstance(e, WarningEvent) for e in events) == transcript_fails
            assert not runtime._writer.held
            assert [e async for e in runtime.stream_close()] == events
            assert order == ["model_close", "second", "first", "repository_close"]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
