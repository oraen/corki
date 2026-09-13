"""Committed async prompt feedback must survive loss of its in-memory owner."""

import asyncio
import json
import os
import shlex
import sys
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import HookCompleted, HookStarted, TurnCompleted, WarningEvent
from corki.protocol.ids import new_turn_id
from corki.protocol.items import UserMessageItem
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    ("actual_process", "committed", "checkpoint", "projection_fault"),
    [
        (False, False, False, None),
        (False, True, False, None),
        (True, False, False, None),
        (True, True, False, None),
        (False, False, True, None),
        (False, True, True, None),
        (False, True, True, "async_prompt_projection:"),
        (False, True, True, "async_prompt_delivered:"),
    ],
)
def test_async_prompt_result_recovered_without_reexecution(
    tmp_path, monkeypatch, committed, actual_process, checkpoint, projection_fault
):
    async def scenario():
        source = tmp_path / "config.toml"
        command = (
            shlex.join(
                [
                    sys.executable,
                    "-c",
                    "import json,os,sys,time; from pathlib import Path; "
                    "payload=json.load(sys.stdin); "
                    "Path('started-'+str(os.getpid())).write_text(json.dumps(payload)); "
                    "exec(\"while not Path('release').exists(): time.sleep(0.01)\"); "
                    "print(json.dumps({'continue':False,'systemMessage':'ASYNC_PROMPT_WARNING',"
                    "'hookSpecificOutput':{'hookEventName':'UserPromptSubmit',"
                    "'additionalContext':'ASYNC_PROMPT_CONTEXT'}}))",
                ]
            )
            if actual_process
            else "inspect"
        )
        fingerprint, _ = command_identity(
            {"type": "command", "command": command, "async": True}, event_name="UserPromptSubmit"
        )
        document = (
            "[[hooks.UserPromptSubmit]]\n[[hooks.UserPromptSubmit.hooks]]\n"
            f'type="command"\ncommand={json.dumps(command)}\nasync=true\n'
            f"[hooks.state.{json.dumps(f'{source}:user_prompt_submit:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            configuration=LocalConfigState((ConfigLayer(source, "user", contents=document),)),
        )
        calls, requests = [], []
        release = asyncio.Event()

        async def runner(command, payload, **kwargs):
            calls.append(payload)
            await release.wait()
            return {
                "exit_code": 0,
                "stderr": "",
                "stdout": json.dumps(
                    {
                        "continue": False,
                        "systemMessage": "ASYNC_PROMPT_WARNING",
                        "hookSpecificOutput": {
                            "hookEventName": "UserPromptSubmit",
                            "additionalContext": "ASYNC_PROMPT_CONTEXT",
                        },
                    }
                ),
            }

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        async def create(thread=None):
            return await LangGraphRuntime.acreate(
                settings=settings
                if thread is None
                else replace(settings, configuration=LocalConfigState(())),
                model=Model(),
                registry=ToolRegistry(),
                database_path=tmp_path / "state.db",
                home_path=tmp_path,
                thread_id=thread,
            )

        if not actual_process:
            monkeypatch.setattr("corki.core.prompt_hooks.run_command", runner)
        warm = await create()
        try:
            complete = warm._repository.complete_hook_execution

            async def commit(*args):
                if committed:
                    await complete(*args)
                raise OSError("injected async result boundary")

            if not actual_process:
                monkeypatch.setattr(warm._repository, "complete_hook_execution", commit)
            events = []
            if checkpoint:

                class Sink:
                    async def emit(self, event):
                        events.append(event)

                await warm._ensure_ready()
                thread, turn = warm.thread_id, new_turn_id()
                item = UserMessageItem("ORIGINAL", turn)
                await warm._repository.save_turn(
                    TurnRecord(turn, thread, TurnStatus.RUNNING, item.content)
                )
                await warm._compiled.ainvoke(
                    _initial_state(thread, turn, settings, item),
                    config=warm._graph_config(turn),
                    context=GraphRunContext(events=Sink()),
                    interrupt_before=["call_model"],
                )
                saved = await warm._compiled.aget_state(warm._graph_config(turn))
                assert saved.next == ("call_model",)
                assert not requests
            else:
                events = [e async for e in warm.stream("ORIGINAL")]
                assert isinstance(events[-1], TurnCompleted)
                thread, turn = warm.thread_id, events[-1].turn_id
            assert not any(isinstance(e, (HookStarted, HookCompleted)) for e in events)
            if actual_process:
                async with asyncio.timeout(5):
                    while not tuple(tmp_path.glob("started-*")):
                        await asyncio.sleep(0.01)
                started = tuple(tmp_path.glob("started-*"))
                assert len(started) == 1
                pid = int(started[0].name.removeprefix("started-"))
                os.kill(pid, 0)
                calls.append(json.loads(started[0].read_text()))
                assert warm._graph._stop_hooks._async._tasks
                if committed:
                    (tmp_path / "release").touch()
            release.set()
            if not actual_process or committed:
                async with asyncio.timeout(5):
                    while warm._graph._stop_hooks._async._tasks:
                        await asyncio.sleep(0.01)
            assert len(calls) == 1
            facts = await warm._repository.load_hook_executions(thread, turn, "user_prompt_submit:")
            assert len(facts) == 1 and (facts[0][2] is not None) == committed
        finally:
            await warm.aclose()
        if actual_process:
            assert not warm._graph._stop_hooks._async._tasks
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
        cold = await create(thread)
        projection_events = []
        if projection_fault:
            from corki.core.async_prompt_hooks import project_checkpoint

            class ProjectionSink:
                async def emit(self, event):
                    projection_events.append(event)

            try:
                await cold._ensure_ready()
                snapshot = await cold._compiled.aget_state(cold._graph_config(turn))
                save = cold._repository.save_hook_batch

                async def save_then_interrupt(*args, **kwargs):
                    await save(*args, **kwargs)
                    if args[2].startswith(projection_fault):
                        raise OSError("injected projection interruption")

                monkeypatch.setattr(cold._repository, "save_hook_batch", save_then_interrupt)
                with pytest.raises(OSError, match="injected projection interruption"):
                    await project_checkpoint(
                        cold._repository,
                        thread,
                        turn,
                        ProjectionSink(),
                        checkpoint_id=snapshot.config["configurable"]["checkpoint_id"],
                    )
                assert not requests
            finally:
                await cold.aclose()
            cold = await create(thread)
        try:
            events = [
                e async for e in (cold.resume_pending() if checkpoint else cold.stream("FOLLOW_UP"))
            ]
            assert isinstance(events[-1], TurnCompleted)
            assert len(calls) == 1
            assert ("ASYNC_PROMPT_CONTEXT" in str(requests[-1].items)) == committed
            if checkpoint:
                assert ("ASYNC_PROMPT_CONTEXT" in str(requests[0].items)) == committed
                assert len(requests) == 1
            assert sum(
                isinstance(e, WarningEvent) and e.message == "ASYNC_PROMPT_WARNING"
                for e in [*projection_events, *events]
            ) == int(committed)
            assert not any(isinstance(e, (HookStarted, HookCompleted)) for e in events)
            assert (
                await cold._repository.load_hook_executions(thread, turn, "user_prompt_submit:")
                == facts
            )
        finally:
            await cold.aclose()
        again = await create(thread)
        try:
            events = [e async for e in again.stream("SECOND_RESTART")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(calls) == 1
            assert not any(
                isinstance(e, WarningEvent) and e.message == "ASYNC_PROMPT_WARNING" for e in events
            )
            history = await again._repository.load_items(thread)
            assert sum(
                getattr(item, "content", None) == "ASYNC_PROMPT_CONTEXT" for item in history
            ) == int(committed)
            if actual_process:
                assert len(tuple(tmp_path.glob("started-*"))) == 1
        finally:
            await again.aclose()

    asyncio.run(scenario())
