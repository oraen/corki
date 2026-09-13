"""Actual async hook processes outlive compaction, not their Runtime owner."""

import asyncio
import json
import os
import shlex
import sys

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import HookCompleted, HookStarted, TurnCompleted, WarningEvent
from corki.protocol.ids import ToolCallId
from corki.protocol.items import AssistantMessageItem, CompactionItem, ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("event", ["PreCompact", "PostCompact"])
@pytest.mark.parametrize("finish", ["complete", "close"])
@pytest.mark.parametrize("automatic", [False, True])
def test_async_compact_hook_control_is_ignored_and_process_is_owned(
    tmp_path, event, finish, automatic
):
    async def scenario():
        effects, summaries, normal = [], [], []

        class Large:
            spec = ToolSpec("large", "large observation", {"type": "object"})

            async def execute(self, call, context):
                effects.append(call.id)
                return ToolResult(call.id, call.name, "x" * 16000)

        class Model:
            async def stream(self, request):
                if getattr(request.items[-1], "content", None) == "SUMMARY_REQUEST":
                    summaries.append(request)
                    yield ModelCompleted(
                        (AssistantMessageItem("summary", request.items[-1].turn_id, new_step_id()),)
                    )
                elif automatic and not normal:
                    normal.append(request)
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(ToolCallId("large-call"), "large", {}),
                                request.items[-1].turn_id,
                                new_step_id(),
                            ),
                        )
                    )
                else:
                    normal.append(request)
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        command = shlex.join(
            [
                sys.executable,
                "-c",
                "import json,os,sys,time; from pathlib import Path; json.load(sys.stdin); "
                "Path('started-'+str(os.getpid())).write_text(str(os.getpid())); "
                "exec(\"while not Path('release').exists(): time.sleep(0.01)\"); "
                "print(json.dumps({'continue':False,'stopReason':'DO_NOT_CONTROL',"
                "'systemMessage':'ASYNC_COMPACT_WARNING'}))",
            ]
        )
        fingerprint, _ = command_identity(
            {"type": "command", "command": command, "async": True}, event_name=event
        )
        source = tmp_path / "config.toml"
        key = "pre_compact" if event == "PreCompact" else "post_compact"
        document = (
            f'[[hooks.{event}]]\n[[hooks.{event}.hooks]]\ntype="command"\nasync=true\n'
            f"command={json.dumps(command)}\n[hooks.state.{json.dumps(f'{source}:{key}:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            tool_mode="direct",
            context_window_tokens=8000,
            auto_compact_tokens=3000,
            compact_prompt="SUMMARY_REQUEST",
            configuration=LocalConfigState((ConfigLayer(source, "user", contents=document),)),
        )

        async def create(thread=None):
            registry = ToolRegistry()
            registry.register(Large())
            return await LangGraphRuntime.acreate(
                settings=settings,
                model=Model(),
                registry=registry,
                database_path=tmp_path / "state.db",
                home_path=tmp_path,
                thread_id=thread,
            )

        runtime = await create()
        try:

            async def collect():
                stream = (
                    runtime.stream("make a large observation") if automatic else runtime.compact()
                )
                return [e async for e in stream]

            events = await asyncio.wait_for(collect(), 5)
            assert isinstance(events[-1], TurnCompleted)
            thread, turn = runtime.thread_id, events[-1].turn_id
            assert not any(isinstance(e, (HookStarted, HookCompleted)) for e in events)
            async with asyncio.timeout(5):
                while not tuple(tmp_path.glob("started-*")):
                    await asyncio.sleep(0.01)
            started = tuple(tmp_path.glob("started-*"))
            assert len(started) == 1
            pid = int(started[0].read_text())
            os.kill(pid, 0)
            owner = runtime._graph._stop_hooks._async
            assert len(owner._tasks) == 1
            assert (
                sum(
                    isinstance(i, CompactionItem)
                    for i in await runtime._repository.load_items(thread)
                )
                == 1
            )
            if finish == "complete":
                (tmp_path / "release").touch()
                async with asyncio.timeout(5):
                    while owner._tasks:
                        await asyncio.sleep(0.01)
                follow = [e async for e in runtime.stream("continue")]
                assert isinstance(follow[-1], TurnCompleted)
                assert (
                    sum(
                        isinstance(e, WarningEvent) and e.message == "ASYNC_COMPACT_WARNING"
                        for e in follow
                    )
                    == 1
                )
                assert not any(isinstance(e, (HookStarted, HookCompleted)) for e in follow)
        finally:
            await runtime.aclose()
        assert not owner._tasks
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        cold = await create(thread)
        try:
            await cold._ensure_ready()
            records = await cold._repository.load_hook_executions(thread, turn, "compact_hook:")
            assert len(records) == 1
            assert records[0][1]["payload"]["trigger"] == ("auto" if automatic else "manual")
            assert records[0][1]["payload"]["hook_event_name"] == event
            assert (records[0][2] is None) == (finish == "close")
            assert [e async for e in cold.resume_pending()] == []
            follow = [e async for e in cold.stream("cold continuation")]
            assert isinstance(follow[-1], TurnCompleted)
            assert not any(isinstance(e, (HookStarted, HookCompleted)) for e in follow)
            assert len(tuple(tmp_path.glob("started-*"))) == 1
            assert len(summaries) == 1
            assert len(effects) == (1 if automatic else 0)
        finally:
            await cold.aclose()

    asyncio.run(scenario())
