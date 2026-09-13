"""Interrupt effects belong to an interrupted root Turn, not every cancellation."""

import asyncio
import json
import shlex
import sys
from hashlib import sha256

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.protocol.events import HookCompleted, HookStarted, TurnCancelled
from corki.protocol.ids import ThreadId
from corki.protocol.session_source import SessionSource, SubAgentSource, ThreadSpawnSource
from corki.tools import ToolRegistry


@pytest.mark.parametrize("origin", ["root", "child", "review"])
@pytest.mark.parametrize("reason", ["interrupted", "replaced"])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_interrupt_hook_runs_before_terminal_only_for_interrupted_root(
    tmp_path, origin, reason, asynchronous
):
    async def scenario():
        gate = (
            "import time\nwhile not Path('release').exists(): time.sleep(0.01)\n"
            if asynchronous
            else ""
        )
        command = shlex.join(
            [
                sys.executable,
                "-c",
                "import json,sys; from pathlib import Path; p=json.load(sys.stdin)\n"
                + gate
                + "Path('interrupt.jsonl').open('a').write(json.dumps({'payload':p,"
                "'transcript':Path(p['transcript_path']).read_text()})+'\\n'); "
                "print(json.dumps({'systemMessage':'INTERRUPT_DIAGNOSTIC'}))",
            ]
        )
        identity = {
            "event_name": "interrupt",
            "hooks": [{"type": "command", "command": command, "timeout": 1, "async": asynchronous}],
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
            "[[hooks.Interrupt]]\n[[hooks.Interrupt.hooks]]\n"
            f'type="command"\ncommand={json.dumps(command)}\ntimeout=1\n'
            f"async={str(asynchronous).lower()}\n"
            f"[hooks.state.{json.dumps(f'{path}:interrupt:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        started = asyncio.Event()
        model_finished = asyncio.Event()

        class Model:
            async def stream(self, request):
                started.set()
                try:
                    await asyncio.Event().wait()
                    yield
                finally:
                    model_finished.set()

            async def aclose(self):
                pass

        source = SessionSource()
        if origin == "child":
            source = SessionSource.subagent(
                SubAgentSource("thread_spawn", ThreadSpawnSource(ThreadId("parent"), 1))
            )
        elif origin == "review":
            source = SessionSource.subagent(SubAgentSource("review"))
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                configuration=LocalConfigState((ConfigLayer(path, "user", contents=document),)),
            ),
            model=Model(),
            registry=ToolRegistry(),
            session_source=source,
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )
        events = []

        async def consume():
            async for event in runtime.stream("INTERRUPTED_PROMPT"):
                events.append(event)

        consumer = asyncio.create_task(consume())
        try:
            async with asyncio.timeout(8):
                await started.wait()
                await runtime.cancel_active(reason=reason)
                with pytest.raises(asyncio.CancelledError):
                    await consumer
            assert model_finished.is_set()
            assert isinstance(events[-1], TurnCancelled)
            assert len([event for event in events if isinstance(event, TurnCancelled)]) == 1
            expected = origin == "root" and reason == "interrupted"
            log = tmp_path / "interrupt.jsonl"
            if expected and asynchronous:
                assert not log.exists(), (
                    "Async interrupt must not hold the terminal until completion"
                )
                (tmp_path / "release").write_text("continue")
                async with asyncio.timeout(5):
                    while True:
                        _, records = await runtime._repository.load_hook_batch(
                            runtime.thread_id,
                            events[-1].turn_id,
                            f"interrupt_hook:{events[-1].turn_id}:",
                        )
                        if records and all(
                            record["result"] is not None for record in records.values()
                        ):
                            break
                        await asyncio.sleep(0.01)
            assert log.exists() == expected, "Interrupt dispatch does not match cancellation origin"
            hooks = [event for event in events if isinstance(event, (HookStarted, HookCompleted))]
            if expected:
                if asynchronous:
                    assert hooks == []
                else:
                    assert len(hooks) == 2
                    assert isinstance(hooks[0], HookStarted)
                    assert isinstance(hooks[1], HookCompleted)
                    assert hooks[1].run.status == "completed"
                    assert hooks[0].turn_id == hooks[1].turn_id == events[-1].turn_id
                records = [json.loads(line) for line in log.read_text().splitlines()]
                assert len(records) == 1
                payload = records[0]["payload"]
                assert set(payload) == {
                    "session_id",
                    "turn_id",
                    "transcript_path",
                    "cwd",
                    "hook_event_name",
                    "model",
                    "permission_mode",
                }
                assert payload["session_id"] == str(runtime.session_id)
                assert payload["turn_id"] == str(events[-1].turn_id)
                assert payload["hook_event_name"] == "Interrupt"
                assert "INTERRUPTED_PROMPT" in records[0]["transcript"]
                if not asynchronous:
                    assert any(
                        entry.text == "INTERRUPT_DIAGNOSTIC" for entry in hooks[1].run.entries
                    )
            else:
                assert hooks == []
            await runtime.cancel_active()
            await runtime.aclose()
            if expected:
                assert len(log.read_text().splitlines()) == 1
        finally:
            await runtime.aclose()
            await asyncio.gather(consumer, return_exceptions=True)

    asyncio.run(scenario())
