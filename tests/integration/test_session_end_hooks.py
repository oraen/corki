"""SessionEnd is root-only teardown, not a model-visible stop decision."""

import asyncio
import json
import os
import shlex
import sys
from hashlib import sha256

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import HookCompleted, HookStarted, TurnCompleted
from corki.protocol.ids import ThreadId
from corki.protocol.session_source import SessionSource, SubAgentSource, ThreadSpawnSource
from corki.tools import ToolRegistry


@pytest.mark.parametrize("origin", ["root", "child", "review"])
@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_session_end_runs_once_after_turn_and_preserves_transcript(
    tmp_path, origin, fails, asynchronous
):
    async def scenario():
        command = shlex.join(
            [
                sys.executable,
                "-c",
                "import json,sys; from pathlib import Path; p=json.load(sys.stdin); "
                "record={'payload':p,'transcript':Path(p['transcript_path']).read_text()}; "
                "Path('end.jsonl').open('a').write(json.dumps(record)+'\\n'); "
                "print(json.dumps({'continue':False,'decision':'block','reason':'ignore me'})); "
                f"sys.exit({2 if fails else 0})",
            ]
        )
        identity = {
            "event_name": "session_end",
            "matcher": "other",
            "hooks": [
                {"type": "command", "command": command, "timeout": 1, "async": asynchronous},
            ],
        }
        fingerprint = (
            "sha256:"
            + sha256(
                json.dumps(
                    identity, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                ).encode()
            ).hexdigest()
        )
        path = tmp_path / "config.toml"
        document = (
            '[[hooks.SessionEnd]]\nmatcher="other"\n[[hooks.SessionEnd.hooks]]\n'
            f'type="command"\ncommand={json.dumps(command)}\ntimeout=1\n'
            f"async={str(asynchronous).lower()}\n"
            f"[hooks.state.{json.dumps(f'{path}:session_end:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        provenance = SessionSource()
        if origin == "child":
            provenance = SessionSource.subagent(
                SubAgentSource("thread_spawn", ThreadSpawnSource(ThreadId("parent"), 1))
            )
        elif origin == "review":
            provenance = SessionSource.subagent(SubAgentSource("review"))
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                configuration=LocalConfigState((ConfigLayer(path, "user", contents=document),)),
            ),
            model=Model(),
            registry=ToolRegistry(),
            session_source=provenance,
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )
        try:
            events = [e async for e in runtime.stream("SESSION_PROMPT")]
            assert isinstance(events[-1], TurnCompleted)
            session = runtime.session_id
            assert not (tmp_path / "end.jsonl").exists(), (
                "SessionEnd ran at Turn end, not session shutdown"
            )
        finally:
            shutdown = [event async for event in runtime.stream_close()]
        await runtime.aclose()
        assert [event async for event in runtime.stream_close()] == shutdown
        log = tmp_path / "end.jsonl"
        assert log.exists() == (origin == "root"), "SessionEnd shutdown dispatch mismatch"
        assert len(requests) == 1
        if origin == "root":
            hooks = [event for event in shutdown if isinstance(event, (HookStarted, HookCompleted))]
            assert len(hooks) == 2
            assert isinstance(hooks[0], HookStarted)
            assert isinstance(hooks[1], HookCompleted)
            assert hooks[0].turn_id == hooks[1].turn_id != events[-1].turn_id
            assert hooks[1].run.status == ("failed" if fails else "completed")
            assert all(entry.kind == "error" for entry in hooks[1].run.entries)
            records = [json.loads(line) for line in log.read_text().splitlines()]
            assert len(records) == 1
            payload = records[0]["payload"]
            assert set(payload) == {
                "session_id",
                "transcript_path",
                "cwd",
                "hook_event_name",
                "reason",
            }
            assert payload["session_id"] == str(session)
            assert payload["hook_event_name"] == "SessionEnd"
            assert payload["reason"] == "other"
            assert "SESSION_PROMPT" in records[0]["transcript"]
        else:
            assert shutdown == []

    asyncio.run(scenario())


@pytest.mark.parametrize("failed_appends", [1, 2])
def test_session_end_waits_for_pending_input_storage_barrier(tmp_path, monkeypatch, failed_appends):
    from corki.core.stop_hooks import StopDecision, command_identity
    from corki.protocol.events import TurnFailed

    async def scenario():
        command = shlex.join(
            [
                sys.executable,
                "-c",
                "import json,sys; from pathlib import Path; p=json.load(sys.stdin); "
                "Path('end.jsonl').open('a').write("
                "json.dumps(Path(p['transcript_path']).read_text())+'\\n')",
            ]
        )
        fingerprint, _ = command_identity(
            {"type": "command", "command": command}, event_name="SessionEnd"
        )
        path = tmp_path / "config.toml"
        document = (
            "[[hooks.SessionEnd]]\n[[hooks.SessionEnd.hooks]]\n"
            f'type="command"\ncommand={json.dumps(command)}\n'
            f"[hooks.state.{json.dumps(f'{path}:session_end:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        model_closes = []

        class Model:
            async def stream(self, request):
                yield ModelCompleted(())

            async def aclose(self):
                model_closes.append(True)

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                configuration=LocalConfigState((ConfigLayer(path, "user", contents=document),)),
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )
        append = runtime._repository.append_items
        attempts = 0

        async def failing_append(thread, items):
            nonlocal attempts
            if any(getattr(item, "content", None) == "LATE_INPUT" for item in items):
                attempts += 1
                if attempts <= failed_appends:
                    raise OSError("late input storage unavailable")
            await append(thread, items)

        async def stop(*args, **kwargs):
            await runtime.steer("LATE_INPUT")
            return StopDecision.STOP

        monkeypatch.setattr(runtime._repository, "append_items", failing_append)
        monkeypatch.setattr(runtime._graph._stop_hooks, "run", stop)
        try:
            events = [event async for event in runtime.stream("INITIAL", realtime=True)]
            assert isinstance(events[-1], TurnFailed)
            assert runtime._input_flush_pending
            assert [item.content for item in runtime._realtime.unrecorded_items] == ["LATE_INPUT"]
            if failed_appends == 2:
                with pytest.raises(OSError, match="late input storage unavailable"):
                    await runtime.aclose()
                assert runtime._close_storage_pending
                assert not (tmp_path / "end.jsonl").exists()
                assert runtime._shutdown_events.items == []
            shutdown = [event async for event in runtime.stream_close()]
            assert not runtime._input_flush_pending
            assert not runtime._realtime.unrecorded_items
            await runtime.aclose()
            assert [event async for event in runtime.stream_close()] == shutdown
            records = [
                json.loads(line) for line in (tmp_path / "end.jsonl").read_text().splitlines()
            ]
            assert len(records) == 1
            assert "INITIAL" in records[0]
            assert "LATE_INPUT" in records[0]
            assert len([event for event in shutdown if isinstance(event, HookCompleted)]) == 1
            assert attempts == failed_appends + 1
            assert model_closes == [True]
        finally:
            monkeypatch.setattr(runtime._repository, "append_items", append)
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("timeout", [None, 99])
@pytest.mark.parametrize("leave_observer", [False, True])
def test_session_end_timeout_reaps_process_when_observer_leaves(tmp_path, timeout, leave_observer):
    from corki.core.stop_hooks import command_identity

    async def scenario():
        command = shlex.join(
            [
                sys.executable,
                "-c",
                "import os,time; from pathlib import Path; "
                "Path('pid').write_text(str(os.getpid())); time.sleep(30)",
            ]
        )
        handler = {"type": "command", "command": command}
        if timeout is not None:
            handler["timeout"] = timeout
        fingerprint, _ = command_identity(handler, event_name="SessionEnd")
        path = tmp_path / "config.toml"
        definition = f"timeout={timeout}\n" if timeout is not None else ""
        document = (
            "[[hooks.SessionEnd]]\n[[hooks.SessionEnd.hooks]]\n"
            f'type="command"\ncommand={json.dumps(command)}\n{definition}'
            f"[hooks.state.{json.dumps(f'{path}:session_end:0:0')}]\ntrusted_hash={json.dumps(fingerprint)}\n"
        )

        class Model:
            async def stream(self, request):
                raise AssertionError("Closing must not sample the model")
                yield

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                configuration=LocalConfigState((ConfigLayer(path, "user", contents=document),)),
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )
        try:
            await runtime._ensure_ready()
            commands, _ = runtime._graph._stop_hooks._snapshot["SessionEnd"]
            assert commands[0].timeout == (1 if timeout is None else 3)
            observer = runtime.stream_close()
            async with asyncio.timeout(8):
                while not isinstance(await anext(observer), HookStarted):
                    pass
                assert not runtime._close_task.done()
                while not (tmp_path / "pid").exists():
                    await asyncio.sleep(0.01)
                pid = int((tmp_path / "pid").read_text())
                os.kill(pid, 0)
                if leave_observer:
                    await observer.aclose()
                    await runtime.aclose()
                else:
                    remaining = [event async for event in observer]
                    assert isinstance(remaining[-1], HookCompleted)
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
            replay = [event async for event in runtime.stream_close()]
            completed = [event for event in replay if isinstance(event, HookCompleted)]
            assert len(completed) == 1 and completed[0].run.status == "failed"
            assert completed[0].run.entries
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
