"""Completed async feedback must survive a cold call_model checkpoint."""

import asyncio
import json
import shlex
import sqlite3
import sys
from dataclasses import replace

import pytest
from test_code_mode import request_call

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import TurnCancelled, TurnCompleted
from corki.protocol.ids import new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ContextItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolResult, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    "event_name,projection_interrupted",
    [
        (event, interrupted)
        for event in ("PreToolUse", "PostToolUse")
        for interrupted in (False, True)
    ]
    + [(event, "compact") for event in ("PreToolUse", "PostToolUse")],
)
def test_cold_model_checkpoint_receives_completed_async_context(
    tmp_path, monkeypatch, event_name, projection_interrupted
):
    async def scenario():
        command = shlex.join(
            [
                sys.executable,
                "-c",
                (
                    "import json,time; from pathlib import Path; "
                    "Path('count').open('a').write('once\\n'); "
                    "exec(\"while not Path('release').exists(): time.sleep(0.01)\"); "
                    f"print(json.dumps({{'hookSpecificOutput':{{'hookEventName':'{event_name}',"
                    "'additionalContext':'CHECKPOINT_ASYNC'}}))"
                ),
            ]
        )
        fingerprint = command_identity(
            {"type": "command", "command": command, "async": True},
            event_name=event_name,
            matcher="probe",
        )[0]
        source = tmp_path / "config.toml"
        label = "pre_tool_use" if event_name == "PreToolUse" else "post_tool_use"
        document = (
            f"[[hooks.{event_name}]]\nmatcher='probe'\n[[hooks.{event_name}.hooks]]\n"
            f"type='command'\nasync=true\ncommand={json.dumps(command)}\n"
            f"[hooks.state.{json.dumps(f'{source}:{label}:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            tool_mode="direct",
            configuration=LocalConfigState((ConfigLayer(source, "user", contents=document),)),
        )
        requests, effects, summaries = [], [], []
        entered, committed = asyncio.Event(), asyncio.Event()

        class Probe:
            spec = ToolSpec("probe", "fixture", {"type": "object"})

            async def execute(self, call, context):
                effects.append("once")
                return ToolResult(call.id, call.name, "done")

        class Model:
            async def stream(self, request):
                if not request.tools:
                    summaries.append(request)
                    assert any(
                        isinstance(item, ContextItem) and item.content == "CHECKPOINT_ASYNC"
                        for item in request.items
                    )
                    yield ModelCompleted(
                        (
                            AssistantMessageItem(
                                "CHECKPOINT_SUMMARY", request.items[-1].turn_id, new_step_id()
                            ),
                        )
                    )
                    return
                requests.append(request)
                if len(requests) == 1:
                    yield request_call(request, "probe", {})
                elif len(requests) == 2:
                    (tmp_path / "release").touch()
                    await committed.wait()
                    entered.set()
                    await asyncio.Event().wait()
                else:
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        class Sink:
            async def emit(self, event):
                pass

        async def create(thread=None, config=settings):
            registry = ToolRegistry()
            registry.register(Probe())
            return await LangGraphRuntime.acreate(
                settings=replace(
                    config,
                    memories_enabled=True,
                    memories_generate=False,
                    memories_background_enabled=False,
                    memories_disable_on_external_context=True,
                ),
                registry=registry,
                model=Model(),
                home_path=tmp_path,
                database_path=tmp_path / "state.db",
                thread_id=thread,
            )

        warm = await create()
        try:
            await warm._ensure_ready()
            await warm.set_thread_memory_mode("enabled")
            thread, turn = warm.thread_id, new_turn_id()
            user = UserMessageItem("probe", turn)
            await warm._repository.save_turn(TurnRecord(turn, thread, TurnStatus.RUNNING, "probe"))
            await warm._repository.append_items(thread, (user,))
            complete = warm._repository.complete_hook_execution

            async def observed(*args):
                await complete(*args)
                committed.set()

            warm._repository.complete_hook_execution = observed
            task = asyncio.create_task(
                warm._compiled.ainvoke(
                    _initial_state(thread, turn, settings, user),
                    context=GraphRunContext(events=Sink()),
                    config=warm._graph_config(turn),
                    durability="sync",
                )
            )
            try:
                await asyncio.wait_for(entered.wait(), 5)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            checkpoint = await warm._compiled.aget_state(warm._graph_config(turn))
            assert checkpoint.next == ("call_model",)
            records = await warm._repository.load_hook_executions(thread, turn, label + ":")
            assert len(records) == 1 and records[0][2]["exit_code"] == 0
        finally:
            await warm.aclose()
        cold = await create(
            thread,
            CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                tool_mode="direct",
                configuration=LocalConfigState(()),
            ),
        )
        try:
            if projection_interrupted:
                from corki.core import post_hook_recovery

                await cold._ensure_ready()
                if projection_interrupted == "compact":
                    projected = asyncio.Event()
                    project = (
                        cold._graph._async_pre_hooks.project_checkpoint
                        if event_name == "PreToolUse"
                        else post_hook_recovery.recover_feedback
                    )

                    async def suspended_projection(*args, **kwargs):
                        feedback = await project(*args, **kwargs)
                        assert [item.content for item in feedback] == ["CHECKPOINT_ASYNC"]
                        projected.set()
                        await asyncio.Event().wait()

                    if event_name == "PreToolUse":
                        monkeypatch.setattr(
                            cold._graph._async_pre_hooks, "project_checkpoint", suspended_projection
                        )
                    else:
                        monkeypatch.setattr(
                            post_hook_recovery, "recover_feedback", suspended_projection
                        )

                    async def resume():
                        events = []
                        try:
                            async for event in cold.resume_pending():
                                events.append(event)
                        except asyncio.CancelledError:
                            assert events and isinstance(events[-1], TurnCancelled)
                        return events

                    resumed = asyncio.create_task(resume())
                    try:
                        await asyncio.wait_for(projected.wait(), 5)
                        compacted = [event async for event in cold.compact()]
                        assert isinstance(compacted[-1], TurnCompleted), compacted[-1]
                        cancelled = await asyncio.wait_for(resumed, 5)
                        assert isinstance(cancelled[-1], TurnCancelled), cancelled[-1]
                    finally:
                        if not resumed.done():
                            resumed.cancel()
                        await asyncio.gather(resumed, return_exceptions=True)
                    assert len(requests) == 2 and len(summaries) == 1
                    await cold.aclose()
                    cold = await create(thread)
                    assert [event async for event in cold.resume_pending()] == []
                    events = [event async for event in cold.stream("continue after compact")]
                    assert isinstance(events[-1], TurnCompleted), events[-1]
                    assert any(
                        isinstance(item, CompactionItem) and "CHECKPOINT_SUMMARY" in item.summary
                        for item in requests[2].items
                    )
                    assert not any(
                        isinstance(item, ContextItem) and item.content == "CHECKPOINT_ASYNC"
                        for item in requests[2].items
                    )
                    history = await cold._repository.load_items(thread)
                    assert (
                        sum(
                            isinstance(item, ContextItem) and item.content == "CHECKPOINT_ASYNC"
                            for item in history
                        )
                        == 1
                    )
                    assert effects == ["once"]
                    assert (tmp_path / "count").read_text() == "once\n"
                    return
                checkpoint = await cold._compiled.aget_state(cold._graph_config(turn))
                checkpoint_id = checkpoint.config["configurable"]["checkpoint_id"]
                if event_name == "PreToolUse":
                    feedback = await cold._graph._async_pre_hooks.project_checkpoint(
                        cold._repository,
                        Sink(),
                        thread=thread,
                        turn=turn,
                        checkpoint_id=checkpoint_id,
                    )
                else:
                    feedback = await post_hook_recovery.recover_feedback(
                        cold._repository, thread, turn, checkpoint_id=checkpoint_id
                    )
                assert [item.content for item in feedback] == ["CHECKPOINT_ASYNC"]
                # Projection/history committed, but the sampling checkpoint is still old.
                await cold.aclose()
                cold = await create(
                    thread,
                    CorkiSettings(
                        tmp_path,
                        skills_enabled=False,
                        plugins_enabled=False,
                        tool_mode="direct",
                        configuration=LocalConfigState(()),
                    ),
                )
            events = [event async for event in cold.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert [
                i.content
                for i in requests[2].items
                if isinstance(i, ContextItem) and i.content_kind == "hooks.additional_context"
            ] == ["CHECKPOINT_ASYNC"]
            assert effects == ["once"]
            assert (tmp_path / "count").read_text() == "once\n"
        finally:
            await cold.aclose()
            with sqlite3.connect(tmp_path / "state.db") as db:
                assert db.execute(
                    "SELECT memory_mode FROM threads WHERE id=?", (thread,)
                ).fetchone() == ("enabled",)

    asyncio.run(scenario())
