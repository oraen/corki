"""A saved start decision survives a cold Runtime and removal of its configuration."""

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
from corki.protocol.events import TurnCompleted, TurnFailed
from corki.protocol.ids import ToolCallId, new_turn_id
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ToolCallItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    ("start_path", "window"),
    [
        ("initial", "planned"),
        ("initial", "unknown"),
        ("initial", "completed"),
        ("initial", "receipt"),
        ("clear", "planned"),
        ("clear", "unknown"),
        ("clear", "completed"),
        ("clear", "receipt"),
        ("manual", "consumer_before"),
        ("manual", "consumer_after"),
        ("manual", "planned"),
        ("manual", "unknown"),
        ("manual", "completed"),
        ("manual", "receipt"),
        ("manual", "done"),
        ("auto", "installed"),
        ("auto", "consumer_before"),
        ("auto", "consumer_after"),
        ("auto", "planned"),
        ("auto", "unknown"),
        ("auto", "completed"),
        ("auto", "receipt"),
        ("auto", "done"),
    ],
)
@pytest.mark.parametrize("stop", [False, True])
@pytest.mark.parametrize("retain_configuration", [False, True])
def test_start_hook_cold_recovery(
    tmp_path, monkeypatch, window, stop, start_path, retain_configuration
):
    async def scenario():
        compact, auto = start_path in {"manual", "auto"}, start_path == "auto"
        consumer_boundary = window in {"consumer_before", "consumer_after"}
        before_selection = consumer_boundary or window == "installed"
        before_execution = before_selection or window == "planned"
        batch_boundary = before_execution or window in {"receipt", "done"}
        initial_source = "clear" if start_path == "clear" else "startup"
        source = tmp_path / "config.toml"
        fingerprint, _ = command_identity(
            {"type": "command", "command": "inspect"},
            event_name="SessionStart",
            matcher="compact" if compact else initial_source,
        )
        matcher = f"matcher={json.dumps('compact' if compact else initial_source)}\n"
        document = (
            f'[[hooks.SessionStart]]\n{matcher}[[hooks.SessionStart.hooks]]\ntype="command"\ncommand="inspect"\n'
            f"[hooks.state.{json.dumps(f'{source}:session_start:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            compact_prompt="SUMMARIZE_FOR_TEST",
            context_window_tokens=8000,
            auto_compact_tokens=3000,
            configuration=LocalConfigState((ConfigLayer(source, "user", contents=document),)),
        )
        calls, requests, summaries, effects = [], [], [], []

        class Large:
            spec = ToolSpec("large", "large observation", {"type": "object"})

            async def execute(self, call, context):
                effects.append(call.id)
                return ToolResult(call.id, call.name, "x" * 16000)

        async def runner(command, payload, **kwargs):
            calls.append(payload)
            return {
                "exit_code": 0,
                "stderr": "",
                "stdout": json.dumps(
                    {
                        "continue": not stop,
                        "hookSpecificOutput": {
                            "hookEventName": "SessionStart",
                            "additionalContext": "SAVED_START",
                        },
                    }
                ),
            }

        class Sink:
            async def emit(self, event):
                pass

        class Model:
            async def stream(self, request):
                if getattr(request.items[-1], "content", "") == "SUMMARIZE_FOR_TEST":
                    summaries.append(request)
                    yield ModelCompleted(
                        (
                            AssistantMessageItem(
                                "SAVED SUMMARY", request.items[-1].turn_id, new_step_id()
                            ),
                        )
                    )
                    return
                requests.append(request)
                if auto and len(requests) == 1:
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(ToolCallId("large-result"), "large", {}),
                                request.items[-1].turn_id,
                                new_step_id(),
                            ),
                        )
                    )
                    return
                yield ModelCompleted(())

            async def aclose(self):
                pass

        async def create(thread=None):
            registry = ToolRegistry()
            if auto:
                registry.register(Large())
            return await LangGraphRuntime.acreate(
                settings=settings
                if thread is None or retain_configuration
                else replace(settings, configuration=LocalConfigState(())),
                model=Model(),
                registry=registry,
                database_path=tmp_path / "state.db",
                home_path=tmp_path,
                thread_id=thread,
                session_start_source="clear" if start_path == "clear" and thread is None else None,
            )

        monkeypatch.setattr("corki.core.start_hooks.run_command", runner)
        warm = await create()
        try:
            await warm._ensure_ready()
            if start_path == "manual":
                assert isinstance([e async for e in warm.compact()][-1], TurnCompleted)
                assert not calls
            thread, turn = warm.thread_id, new_turn_id()
            item = UserMessageItem("FIRST", turn)
            await warm._repository.save_turn(
                TurnRecord(turn, thread, TurnStatus.RUNNING, item.content)
            )
            complete, save = (
                warm._repository.complete_hook_execution,
                warm._repository.save_hook_batch,
            )

            async def commit(*args):
                if window == "completed":
                    await complete(*args)
                raise OSError("injected start boundary")

            async def receipt(*args):
                binding = args[2].startswith("compact_start_source:") and args[2].endswith(
                    ":consumer"
                )
                if window == "consumer_before" and binding:
                    raise OSError("injected start boundary")
                await save(*args)
                if (
                    window == "consumer_after"
                    and binding
                    or window == "planned"
                    and args[2].startswith("start_hook:")
                    and not args[2].endswith(":receipt")
                    or window == "receipt"
                    and args[2].startswith("start_hook:")
                    and args[2].endswith(":receipt")
                    or window == "done"
                    and args[2].startswith("compact_start_source:")
                    and args[2].endswith(":done")
                ):
                    raise OSError("injected start boundary")

            append = warm._repository.append_items

            async def installed_boundary(thread_id, items):
                await append(thread_id, items)
                if any(isinstance(i, CompactionItem) for i in items):
                    raise OSError("injected start boundary")

            with monkeypatch.context() as patch:
                if window == "installed":
                    patch.setattr(warm._repository, "append_items", installed_boundary)
                else:
                    patch.setattr(
                        warm._repository,
                        "save_hook_batch" if batch_boundary else "complete_hook_execution",
                        receipt if batch_boundary else commit,
                    )
                with pytest.raises(OSError, match="injected start boundary"):
                    await warm._compiled.ainvoke(
                        _initial_state(thread, turn, settings, item),
                        config=warm._graph_config(turn),
                        context=GraphRunContext(events=Sink()),
                    )
            assert len(calls) == int(not before_execution) and len(requests) == int(auto)
            facts = await warm._repository.load_hook_executions(thread, turn, "start_hook:")
            assert len(facts) == int(not before_execution)
            if before_selection:
                marker = next(
                    i
                    for i in await warm._repository.load_items(thread)
                    if isinstance(i, CompactionItem)
                )
                binding_key = f"compact_start_source:{marker.id}:consumer"
                binding = await warm._repository.load_hook_batch(
                    thread, marker.turn_id, binding_key
                )
                assert (binding is not None) is (window == "consumer_after")
                if binding is not None:
                    assert binding[0] == {"version": 1, "turn": str(turn)}
            if not compact:
                key = f"start_hook:{turn}:initial:"
                plan_before = (await warm._repository.load_hook_batch(thread, turn, key))[0]
                assert plan_before["payload"]["source"] == initial_source
        finally:
            await warm.aclose()
        cold = await create(thread)
        try:
            events = [e async for e in cold.resume_pending()]
            denied = window == "planned" and not retain_configuration
            failed = window == "unknown" or denied
            hook_selected = not before_selection or retain_configuration
            stopped = stop and hook_selected
            assert isinstance(events[-1], TurnFailed if failed else TurnCompleted)
            if window == "unknown":
                assert "outcome unknown" in events[-1].error
            if denied:
                assert "authorization changed" in events[-1].error
            assert len(calls) == int(not denied and hook_selected)
            if not compact:
                assert all(payload["source"] == initial_source for payload in calls)
            assert len(summaries) == int(compact)
            assert len(effects) == int(auto)
            assert len(requests) == int(auto) + int(not stopped and not failed)
            if not before_execution or denied or not hook_selected:
                assert (
                    await cold._repository.load_hook_executions(thread, turn, "start_hook:")
                    == facts
                )
            if not compact:
                assert (await cold._repository.load_hook_batch(thread, turn, key))[0] == plan_before
            history = await cold._repository.load_items(thread)
            assert sum(isinstance(i, CompactionItem) for i in history) == int(compact)
            originals = [
                i.id
                for i in history
                if isinstance(i, UserMessageItem) and i.retained_from_id is None
            ]
            assert originals == ([item.id] if auto or not stopped and not failed else [])
            assert sum(getattr(i, "content", "") == "SAVED_START" for i in history) == int(
                not failed and hook_selected
            )
            if not stopped and not failed and hook_selected:
                assert "SAVED_START" in str(requests[-1].items)
            assert [e async for e in cold.resume_pending()] == []
        finally:
            await cold.aclose()

    asyncio.run(scenario())
