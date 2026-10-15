"""Cold Runtime recovery across command, context, and handler commit boundaries."""

import asyncio
import json
import shlex
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from test_code_mode import request_call

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted, WarningEvent
from corki.protocol.ids import new_turn_id
from corki.protocol.items import ContextItem, ToolResultItem, UserMessageItem
from corki.protocol.tools import ToolResult, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    "window", ["unknown", "completed", "feedback", "tool_completed", "partial", "reversed"]
)
@pytest.mark.parametrize("remove_configuration", [False, True])
@pytest.mark.parametrize("limit", [0, 20])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
def test_cold_pre_hook_recovery_never_replays_claimed_effects(
    tmp_path, monkeypatch, window, remove_configuration, limit, nested, legacy
):
    async def scenario():
        command = shlex.join(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; import json; "
                "Path('hook-calls').open('a').write('once\\n'); "
                "print(json.dumps({'hookSpecificOutput': "
                "{'additionalContext': 'CHECK_ONCE'*2000}}))",
            ]
        )
        source = tmp_path / "config.toml"
        fingerprint = command_identity(
            {"type": "command", "command": command, "additionalContextLimit": limit},
            event_name="PreToolUse",
            matcher="probe",
        )[0]
        document = (
            "[[hooks.PreToolUse]]\nmatcher='probe'\n[[hooks.PreToolUse.hooks]]\n"
            f"type='command'\ncommand={json.dumps(command)}\n"
            f"additionalContextLimit={limit}\n"
            f"[hooks.state.{json.dumps(f'{source}:pre_tool_use:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        multiple = window in {"partial", "reversed"}
        if multiple:
            second_command = command.replace("CHECK_ONCE", "CHECK_SECOND")
            second_hash = command_identity(
                {"type": "command", "command": second_command, "additionalContextLimit": limit},
                event_name="PreToolUse",
                matcher="probe",
            )[0]
            document += (
                "[[hooks.PreToolUse.hooks]]\ntype='command'\n"
                f"command={json.dumps(second_command)}\nadditionalContextLimit={limit}\n"
                f"[hooks.state.{json.dumps(f'{source}:pre_tool_use:0:1')}]\n"
                f"trusted_hash={json.dumps(second_hash)}\n"
            )
        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            tool_mode="code_mode_only" if nested else "direct",
            configuration=LocalConfigState((ConfigLayer(source, "user", contents=document),)),
        )
        requests, effects = [], []

        class Probe:
            spec = ToolSpec("probe", "test", {"type": "object", "properties": {}})

            async def execute(self, call, context):
                effects.append("once")
                return ToolResult(call.id, call.name, "executed")

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    yield request_call(
                        request,
                        "exec" if nested else "probe",
                        "text(await tools.probe({}));" if nested else {},
                    )
                else:
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        class Sink:
            async def emit(self, event):
                pass

        registry = ToolRegistry()
        registry.register(Probe())
        database = tmp_path / "state.db"
        warm = await LangGraphRuntime.acreate(
            settings=settings,
            registry=registry,
            model=Model(),
            database_path=database,
            home_path=tmp_path,
        )
        try:
            await warm._ensure_ready()
            thread, turn = warm.thread_id, new_turn_id()
            user = UserMessageItem("use probe", turn)
            repository = warm._repository
            await repository.save_turn(TurnRecord(turn, thread, TurnStatus.RUNNING, user.content))
            await repository.append_items(thread, (user,))
            entered = asyncio.Event()
            second_claimed, second_committed = asyncio.Event(), asyncio.Event()
            complete, append, tool_complete = (
                repository.complete_hook_execution,
                repository.append_items,
                repository.complete_tool_call,
            )

            async def held_complete(*args):
                if multiple:
                    if args[2].endswith(":0:1"):
                        await complete(*args)
                        second_committed.set()
                    else:
                        await asyncio.wait_for(second_committed.wait(), 5)
                        if window == "reversed":
                            await complete(*args)
                        entered.set()
                    await asyncio.Event().wait()
                if window != "unknown":
                    await complete(*args)
                if window in {"unknown", "completed"}:
                    entered.set()
                    await asyncio.Event().wait()

            async def held_feedback(thread, items):
                await append(thread, items)
                if window == "feedback" and any(
                    isinstance(i, ContextItem) and i.content_kind == "hooks.additional_context"
                    for i in items
                ):
                    entered.set()
                    await asyncio.Event().wait()

            async def held_tool(*args):
                await tool_complete(*args)
                if window == "tool_completed":
                    entered.set()
                    await asyncio.Event().wait()

            with monkeypatch.context() as patch:
                if legacy or multiple:
                    claim = repository.claim_hook_execution

                    async def legacy_claim(thread, turn, key, request):
                        if legacy:
                            request.pop("feedback")
                        if multiple and key.endswith(":0:0"):
                            await asyncio.wait_for(second_claimed.wait(), 5)
                        result = await claim(thread, turn, key, request)
                        if multiple and key.endswith(":0:1"):
                            second_claimed.set()
                        return result

                    patch.setattr(repository, "claim_hook_execution", legacy_claim)
                patch.setattr(repository, "complete_hook_execution", held_complete)
                patch.setattr(repository, "append_items", held_feedback)
                patch.setattr(repository, "complete_tool_call", held_tool)
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
            original = await repository.load_items(thread)
            if multiple:
                records = await repository.load_hook_executions(
                    thread, turn, f"pre_tool_use:{turn}:"
                )
                assert [key.rsplit(":", 1)[1] for key, _, _ in records] == ["1", "0"]
                assert records[0][2] is not None
                assert (records[1][2] is None) == (window == "partial")
            with repository._connect() as connection:
                hook_facts = [
                    tuple(row) for row in connection.execute("SELECT * FROM hook_executions")
                ]
            assert len(requests) == 1
            assert (tmp_path / "hook-calls").read_text().splitlines() == ["once"] * (
                2 if multiple else 1
            )
        finally:
            await warm.aclose()

        registry = ToolRegistry()
        registry.register(Probe())
        if remove_configuration:
            settings = replace(settings, configuration=LocalConfigState(()))
        cold = await LangGraphRuntime.acreate(
            settings=settings,
            registry=registry,
            model=Model(),
            database_path=database,
            thread_id=thread,
            home_path=tmp_path,
        )
        try:
            events = [event async for event in cold.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 2
            assert (tmp_path / "hook-calls").read_text().splitlines() == ["once"] * (
                2 if multiple else 1
            )
            assert effects == (["once"] if window == "tool_completed" else [])
            result = next(i for i in requests[-1].items if isinstance(i, ToolResultItem))
            assert result.is_error == (window != "tool_completed" or nested)
            feedback = [
                i
                for i in requests[-1].items
                if isinstance(i, ContextItem) and i.content_kind == "hooks.additional_context"
            ]
            if multiple:
                expected = (
                    []
                    if legacy
                    else (
                        ["CHECK_SECOND"] if window == "partial" else ["CHECK_ONCE", "CHECK_SECOND"]
                    )
                )
            else:
                expected = (
                    ["CHECK_ONCE"]
                    if (
                        window in {"feedback", "tool_completed"}
                        or (window == "completed" and not legacy)
                    )
                    else []
                )
            assert len(feedback) == len(expected)
            if legacy and window in {"completed", "partial", "reversed"}:
                assert any(
                    isinstance(event, WarningEvent) and "Legacy PreToolUse" in event.message
                    for event in events
                )
            for fragment, marker in zip(feedback, expected, strict=True):
                if limit == 0:
                    assert fragment.content == marker * 2000
                else:
                    assert "truncated output" in fragment.content
                    path = Path(fragment.content.split("Full hook output saved to: ")[1])
                    assert path.read_text() == marker * 2000
                assert fragment.source_input_id == user.id
            stored = await cold._repository.load_items(thread)
            with cold._repository._connect() as connection:
                assert [
                    tuple(row) for row in connection.execute("SELECT * FROM hook_executions")
                ] == hook_facts
            assert stored[: len(original)] == original
            assert sum(isinstance(i, UserMessageItem) for i in stored) == 1
            assert [event async for event in cold.resume_pending()] == []
        finally:
            await cold.aclose()

    asyncio.run(scenario())
