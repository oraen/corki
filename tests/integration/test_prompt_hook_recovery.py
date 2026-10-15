"""Restart a real RUNNING graph across prompt-hook execution commits."""

import asyncio
import json
from dataclasses import replace

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.ids import new_turn_id
from corki.protocol.items import ContextItem, UserMessageItem
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize("window", ["unknown", "completed", "receipt"])
@pytest.mark.parametrize("policy", ["stop", "context"])
@pytest.mark.parametrize("remove", [False, True])
def test_prompt_hook_cold_recovery_preserves_execution(
    tmp_path, monkeypatch, window, policy, remove
):
    async def scenario():
        source = tmp_path / "config.toml"
        fingerprint, _ = command_identity(
            {"type": "command", "command": "inspect"}, event_name="UserPromptSubmit"
        )
        document = (
            "[[hooks.UserPromptSubmit]]\n[[hooks.UserPromptSubmit.hooks]]\n"
            'type="command"\ncommand="inspect"\n'
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

        async def runner(command, payload, **kwargs):
            calls.append(payload)
            return {
                "exit_code": 0,
                "stderr": "",
                "stdout": json.dumps(
                    {"continue": False}
                    if policy == "stop"
                    else {
                        "hookSpecificOutput": {
                            "hookEventName": "UserPromptSubmit",
                            "additionalContext": "SAVED_CONTEXT",
                        }
                    }
                ),
            }

        monkeypatch.setattr("corki.core.prompt_hooks.run_command", runner)

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        class Sink:
            async def emit(self, event):
                pass

        async def create(thread=None):
            config = (
                replace(settings, configuration=LocalConfigState(()))
                if thread and remove
                else settings
            )
            return await LangGraphRuntime.acreate(
                settings=config,
                model=Model(),
                registry=ToolRegistry(),
                database_path=tmp_path / "state.db",
                home_path=tmp_path,
                thread_id=thread,
            )

        warm = await create()
        try:
            await warm._ensure_ready()
            thread, turn = warm.thread_id, new_turn_id()
            item = UserMessageItem("USER_PROMPT", turn)
            await warm._repository.save_turn(
                TurnRecord(turn, thread, TurnStatus.RUNNING, item.content)
            )
            complete = warm._repository.complete_hook_execution
            save = warm._repository.save_hook_batch

            async def commit(*args):
                if window == "completed":
                    await complete(*args)
                raise OSError("injected prompt commit failure")

            async def receipt(*args):
                await save(*args)
                if args[2].endswith(":receipt"):
                    raise OSError("injected prompt commit failure")

            with monkeypatch.context() as patch:
                if window == "receipt":
                    patch.setattr(warm._repository, "save_hook_batch", receipt)
                else:
                    patch.setattr(warm._repository, "complete_hook_execution", commit)
                with pytest.raises(OSError, match="injected prompt commit failure"):
                    await warm._compiled.ainvoke(
                        _initial_state(thread, turn, settings, item),
                        config=warm._graph_config(turn),
                        context=GraphRunContext(events=Sink()),
                    )
            assert len(calls) == 1 and not requests
            facts = await warm._repository.load_hook_executions(thread, turn, "user_prompt_submit:")
            assert len(facts) == 1
        finally:
            await warm.aclose()
        cold = await create(thread)
        try:
            events = [e async for e in cold.resume_pending()]
            assert isinstance(events[-1], TurnFailed if window == "unknown" else TurnCompleted)
            if window == "unknown":
                assert "outcome unknown" in events[-1].error
            assert len(calls) == 1
            assert len(requests) == int(policy == "context" and window != "unknown")
            assert (
                await cold._repository.load_hook_executions(thread, turn, "user_prompt_submit:")
                == facts
            )
            history = await cold._repository.load_items(thread)
            if window != "unknown":
                assert sum(isinstance(i, UserMessageItem) for i in history) == int(
                    policy == "context"
                )
                assert sum(
                    isinstance(i, ContextItem) and "SAVED_CONTEXT" in i.content for i in history
                ) == int(policy == "context")
                if requests:
                    assert "SAVED_CONTEXT" in str(requests[0].items)
                    items = requests[0].items
                    user_index = next(
                        i for i, value in enumerate(items) if isinstance(value, UserMessageItem)
                    )
                    context_index = next(
                        i
                        for i, value in enumerate(items)
                        if isinstance(value, ContextItem) and "SAVED_CONTEXT" in value.content
                    )
                    assert user_index < context_index
            assert [e async for e in cold.resume_pending()] == []
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("cancelled", [False, True])
def test_admitted_prompt_context_survives_prepare_failure(tmp_path, monkeypatch, cancelled):
    async def scenario():
        source = tmp_path / "config.toml"
        fingerprint, _ = command_identity(
            {"type": "command", "command": "inspect"}, event_name="UserPromptSubmit"
        )
        document = (
            "[[hooks.UserPromptSubmit]]\n[[hooks.UserPromptSubmit.hooks]]\n"
            'type="command"\ncommand="inspect"\n'
            f"[hooks.state.{json.dumps(f'{source}:user_prompt_submit:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        calls = []

        async def runner(command, payload, **kwargs):
            calls.append(payload)
            return {"exit_code": 0, "stdout": "ADMITTED_CONTEXT", "stderr": ""}

        class Model:
            async def stream(self, request):
                raise AssertionError("Must fail before sampling")
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                configuration=LocalConfigState((ConfigLayer(source, "user", contents=document),)),
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )
        try:
            monkeypatch.setattr("corki.core.prompt_hooks.run_command", runner)

            async def fail_prepare(**kwargs):
                assert len(calls) == 1
                raise asyncio.CancelledError() if cancelled else OSError("prepare failed")

            monkeypatch.setattr(runtime._graph._window_manager, "prepare", fail_prepare)
            events = []
            try:
                async for event in runtime.stream("ADMITTED_INPUT"):
                    events.append(event)
            except asyncio.CancelledError:
                assert cancelled
            assert isinstance(events[-1], TurnCancelled if cancelled else TurnFailed)
            history = await runtime._repository.load_items(runtime.thread_id)
            selected = [
                item for item in history if isinstance(item, (UserMessageItem, ContextItem))
            ]
            assert [item.content for item in selected] == ["ADMITTED_INPUT", "ADMITTED_CONTEXT"]
            assert selected[1].source_input_id == selected[0].id
            assert len(calls) == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
