"""Compaction queues a fresh SessionStart before the next ordinary sampling."""

import asyncio
import json
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import TurnCancelled, TurnCompleted
from corki.protocol.ids import ToolCallId
from corki.protocol.items import AssistantMessageItem, CompactionItem, ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.sessions import TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize("trigger", ["manual", "manual_cold", "auto", "auto_twice"])
@pytest.mark.parametrize("stop", [False, True])
def test_compact_start_runs_before_continuation(tmp_path, monkeypatch, trigger, stop):
    async def scenario():
        source = tmp_path / "config.toml"
        fingerprint, _ = command_identity(
            {"type": "command", "command": "inspect"}, event_name="SessionStart", matcher="compact"
        )
        document = (
            '[[hooks.SessionStart]]\nmatcher="compact"\n[[hooks.SessionStart.hooks]]\n'
            'type="command"\ncommand="inspect"\n'
            f"[hooks.state.{json.dumps(f'{source}:session_start:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        calls, summaries, normal, effects = [], [], [], []
        drive_tools = True

        async def runner(command, payload, **kwargs):
            calls.append(payload)
            return {
                "exit_code": 0,
                "stdout": json.dumps(
                    {
                        "continue": not stop,
                        "hookSpecificOutput": {
                            "hookEventName": "SessionStart",
                            "additionalContext": "AFTER_COMPACT",
                        },
                    }
                ),
            }

        class Large:
            spec = ToolSpec("large", "large observation", {"type": "object"})

            async def execute(self, call, context):
                effects.append(call.id)
                return ToolResult(call.id, call.name, "IMPORTANT FACT " + "x" * 16000)

        class Model:
            async def stream(self, request):
                turn, step = request.items[-1].turn_id, new_step_id()
                if getattr(request.items[-1], "content", None) == "SUMMARIZE_FOR_TEST":
                    summaries.append(request)
                    yield ModelCompleted((AssistantMessageItem("IMPORTANT FACT", turn, step),))
                else:
                    normal.append(request)
                    if (
                        drive_tools
                        and trigger.startswith("auto")
                        and len(normal) <= (2 if trigger == "auto_twice" else 1)
                    ):
                        yield ModelCompleted(
                            (
                                ToolCallItem(
                                    ToolCall(
                                        ToolCallId(f"large-result-{len(normal)}"), "large", {}
                                    ),
                                    turn,
                                    step,
                                ),
                            )
                        )
                    else:
                        yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        monkeypatch.setattr("corki.core.start_hooks.run_command", runner)
        registry = ToolRegistry()
        registry.register(Large())
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                tool_mode="direct",
                context_window_tokens=8000,
                auto_compact_tokens=3000,
                compact_prompt="SUMMARIZE_FOR_TEST",
                configuration=LocalConfigState((ConfigLayer(source, "user", contents=document),)),
            ),
            registry=registry,
            model=Model(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )
        try:
            if trigger.startswith("manual"):
                assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
                assert not calls, (
                    "Manual compaction must queue, not execute the next-turn start hook"
                )
                assert not normal
                if trigger == "manual_cold":
                    thread, settings = runtime.thread_id, runtime._settings
                    await runtime.aclose()
                    runtime = await LangGraphRuntime.acreate(
                        settings=settings,
                        registry=ToolRegistry(),
                        model=Model(),
                        database_path=tmp_path / "state.db",
                        home_path=tmp_path,
                        thread_id=thread,
                    )
            events = [e async for e in runtime.stream("CURRENT INPUT")]
            expected = 2 if trigger == "auto_twice" and not stop else 1
            assert len(calls) == expected, "Installed compaction never triggered SessionStart"
            assert calls[0]["source"] == "compact"
            assert calls[0]["session_id"] == str(runtime.session_id)
            assert isinstance(events[-1], TurnCompleted)
            assert len(summaries) == expected
            assert len(effects) == (expected if trigger.startswith("auto") else 0)
            assert len(normal) == (expected if trigger.startswith("auto") else 0) + int(not stop)
            if not stop:
                assert "AFTER_COMPACT" in str(normal[-1].items)
            assert "AFTER_COMPACT" not in str(summaries[0].items)
            if expected == 2:
                assert "AFTER_COMPACT" in str(normal[1].items)
                assert "AFTER_COMPACT" in str(summaries[1].items)
            history = await runtime._repository.load_items(runtime.thread_id)
            assert sum(isinstance(i, CompactionItem) for i in history) == expected
            assert sum(getattr(i, "content", "") == "AFTER_COMPACT" for i in history) == expected
            # A subsequent ordinary turn must not inherit an already consumed source,
            # including one whose hook stopped the original consumer.
            drive_tools = False
            assert isinstance([e async for e in runtime.stream("NEXT INPUT")][-1], TurnCompleted)
            assert len(calls) == expected
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "cold_restart,pending_terminal", [(False, False), (True, False), (False, True)]
)
def test_cancelled_compact_start_does_not_block_next_turn(
    tmp_path, monkeypatch, cold_restart, pending_terminal
):
    async def scenario():
        path = tmp_path / "config.toml"
        fingerprint, _ = command_identity(
            {"type": "command", "command": "inspect"}, event_name="SessionStart", matcher="compact"
        )
        document = (
            '[[hooks.SessionStart]]\nmatcher="compact"\n[[hooks.SessionStart.hooks]]\n'
            'type="command"\ncommand="inspect"\n'
            f"[hooks.state.{json.dumps(f'{path}:session_start:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            compact_prompt="SUMMARIZE_FOR_TEST",
            configuration=LocalConfigState((ConfigLayer(path, "user", contents=document),)),
        )
        calls, requests = [], []

        async def runner(command, payload, **kwargs):
            calls.append(payload)
            raise asyncio.CancelledError

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            "summary or answer", request.items[-1].turn_id, new_step_id()
                        ),
                    )
                )

            async def aclose(self):
                pass

        async def create(thread=None):
            return await LangGraphRuntime.acreate(
                settings=settings,
                model=Model(),
                registry=ToolRegistry(),
                database_path=tmp_path / "state.db",
                home_path=tmp_path,
                thread_id=thread,
            )

        monkeypatch.setattr("corki.core.start_hooks.run_command", runner)
        runtime = await create()
        repository = runtime._repository
        save, retry = repository.save_turn, repository.retry_turn_terminal

        async def unavailable_cancel(record):
            if record.status is TurnStatus.CANCELLED:
                raise OSError("cancel terminal unavailable")
            await save(record)

        try:
            assert isinstance([e async for e in runtime.compact()][-1], TurnCompleted)
            if pending_terminal:
                repository.save_turn = unavailable_cancel
                repository.retry_turn_terminal = unavailable_cancel
            events = []
            with pytest.raises(asyncio.CancelledError):
                async for event in runtime.stream("CANCELLED INPUT"):
                    events.append(event)
            assert isinstance(events[-1], TurnCancelled)
            old_turn, thread = events[-1].turn_id, runtime.thread_id
            facts = await runtime._repository.load_hook_executions(thread, old_turn, "start_hook:")
            assert len(facts) == 1 and facts[0][2] is None
            assert len(calls) == 1 and len(requests) == 1
            if cold_restart:
                await runtime.aclose()
                runtime = await create(thread)
            following = [e async for e in runtime.stream("NEW INPUT")]
            assert isinstance(following[-1], TurnCompleted), following[-1]
            assert len(calls) == 1 and len(requests) == 2
            assert (
                await runtime._repository.load_hook_executions(thread, old_turn, "start_hook:")
                == facts
            )
            assert "CANCELLED INPUT" not in str(requests[-1].items)
            if pending_terminal:
                assert old_turn in runtime._pending_terminals
                assert await repository.load_turn_status(thread, old_turn) is TurnStatus.RUNNING
                repository.save_turn, repository.retry_turn_terminal = save, retry
                assert [e async for e in runtime.resume_pending()] == []
                assert len(calls) == 1 and len(requests) == 2
                assert await repository.load_turn_status(thread, old_turn) is TurnStatus.CANCELLED
        finally:
            repository.save_turn, repository.retry_turn_terminal = save, retry
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("install", [False, True])
def test_manual_source_selects_configuration_when_consumed(tmp_path, monkeypatch, install):
    async def scenario():
        path = tmp_path / "config.toml"
        fingerprint, _ = command_identity(
            {"type": "command", "command": "inspect"}, event_name="SessionStart", matcher="compact"
        )
        document = (
            '[[hooks.SessionStart]]\nmatcher="compact"\n[[hooks.SessionStart.hooks]]\n'
            'type="command"\ncommand="inspect"\n'
            f"[hooks.state.{json.dumps(f'{path}:session_start:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        configuration = LocalConfigState((ConfigLayer(path, "user", contents=document),))
        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            compact_prompt="SUMMARIZE_FOR_TEST",
        )
        calls, requests = [], []

        async def runner(command, payload, **kwargs):
            calls.append(payload)
            return {"exit_code": 0, "stdout": "CONFIG_AT_CONSUMPTION"}

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(
                    (
                        AssistantMessageItem(
                            "summary or answer", request.items[-1].turn_id, new_step_id()
                        ),
                    )
                )

            async def aclose(self):
                pass

        async def create(enabled, thread=None):
            return await LangGraphRuntime.acreate(
                settings=replace(
                    settings, configuration=configuration if enabled else LocalConfigState(())
                ),
                model=Model(),
                registry=ToolRegistry(),
                thread_id=thread,
                database_path=tmp_path / "state.db",
                home_path=tmp_path,
            )

        monkeypatch.setattr("corki.core.start_hooks.run_command", runner)
        warm = await create(not install)
        try:
            assert isinstance([e async for e in warm.compact()][-1], TurnCompleted)
            thread = warm.thread_id
            assert not calls
        finally:
            await warm.aclose()
        cold = await create(install, thread)
        try:
            assert isinstance([e async for e in cold.stream("NEXT INPUT")][-1], TurnCompleted)
            assert len(calls) == int(install)
            assert ("CONFIG_AT_CONSUMPTION" in str(requests[-1].items)) == install
            if calls:
                assert calls[0]["source"] == "compact"
        finally:
            await cold.aclose()

    asyncio.run(scenario())
