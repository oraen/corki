"""Post feedback survives a new Runtime without replaying committed effects."""

import asyncio
import json
import shlex
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
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_turn_id
from corki.protocol.items import ContextItem, ToolResultItem, UserMessageItem
from corki.protocol.tools import ToolResult, ToolSpec
from corki.sessions import TurnRecord, TurnStatus
from corki.tools import ToolRegistry


@pytest.mark.parametrize("window", ["batch", "unknown", "completed"])
@pytest.mark.parametrize("nested", [False, True, "yielded"])
@pytest.mark.parametrize("remove_configuration", [False, True])
@pytest.mark.parametrize("action", ["block", "context"])
@pytest.mark.parametrize("repeat_recovery", [False, "before_append", "after_append"])
def test_cold_post_hook_feedback(
    tmp_path, monkeypatch, window, nested, remove_configuration, action, repeat_recovery
):
    async def scenario():
        command = shlex.join(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; import json; "
                "Path('hook-calls').open('a').write('once\\n'); "
                "print(json.dumps({'decision':'block','reason':'POST_BLOCK',"
                "'hookSpecificOutput':{'hookEventName':'PostToolUse','additionalContext':'SAVED_POST_CONTEXT'}}))",
            ]
        )
        if action == "context":
            command = shlex.join(
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; import json; "
                    "Path('hook-calls').open('a').write('once\\n'); "
                    "print(json.dumps({'hookSpecificOutput':"
                    "{'hookEventName':'PostToolUse','additionalContext':'SAVED_POST_CONTEXT'}}))",
                ]
            )
        source = tmp_path / "config.toml"
        fingerprint = command_identity(
            {"type": "command", "command": command},
            event_name="PostToolUse",
            matcher="probe",
        )[0]
        document = (
            "[[hooks.PostToolUse]]\nmatcher='probe'\n[[hooks.PostToolUse.hooks]]\n"
            f"type='command'\ncommand={json.dumps(command)}\n"
            f"[hooks.state.{json.dumps(f'{source}:post_tool_use:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            tool_mode="code_mode_only" if nested else "direct",
            configuration=LocalConfigState((ConfigLayer(source, "user", contents=document),)),
        )
        requests, effects = [], []
        yielded = nested == "yielded"
        observing = asyncio.Event()

        class Probe:
            spec = ToolSpec("probe", "test", {"type": "object", "properties": {}})

            async def execute(self, call, context):
                effects.append("once")
                return ToolResult(call.id, call.name, "ORIGINAL_RESULT")

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    yield request_call(
                        request,
                        "exec" if nested else "probe",
                        ("yield_control(); " if yielded else "") + "text(await tools.probe({}));"
                        if nested
                        else {},
                    )
                elif yielded and len(requests) == 2:
                    observing.set()
                    await asyncio.Event().wait()
                else:
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        class Sink:
            async def emit(self, event):
                pass

        def registry():
            value = ToolRegistry()
            value.register(Probe())
            return value

        database = tmp_path / "state.db"
        warm = await LangGraphRuntime.acreate(
            settings=settings,
            registry=registry(),
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
            complete, save = repository.complete_hook_execution, repository.complete_tool_call

            async def held_complete(*args):
                if window == "completed":
                    await complete(*args)
                if yielded:
                    await asyncio.wait_for(observing.wait(), 5)
                entered.set()
                await asyncio.Event().wait()

            async def held_batch(*args):
                await save(*args)
                if window == "batch" and args[2].tool_name == "probe":
                    if yielded:
                        await asyncio.wait_for(observing.wait(), 5)
                    entered.set()
                    await asyncio.Event().wait()

            with monkeypatch.context() as patch:
                patch.setattr(repository, "complete_hook_execution", held_complete)
                patch.setattr(repository, "complete_tool_call", held_batch)
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
            assert len(requests) == (2 if yielded else 1)
            if repeat_recovery:
                from corki.core.post_hook_recovery import recover_feedback

                checkpoint = await warm._compiled.aget_state(warm._graph_config(turn))
                checkpoint_id = checkpoint.config["configurable"]["checkpoint_id"]
                if repeat_recovery == "before_append" and (nested or window == "completed"):

                    async def failed_append(*args):
                        raise RuntimeError("injected recovery history failure")

                    with monkeypatch.context() as patch:
                        patch.setattr(repository, "append_items", failed_append)
                        with pytest.raises(RuntimeError, match="injected recovery history"):
                            await recover_feedback(
                                repository, thread, turn, checkpoint_id=checkpoint_id
                            )
                first = await recover_feedback(
                    repository, thread, turn, checkpoint_id=checkpoint_id
                )
                after = await repository.load_items(thread)
                second = await recover_feedback(
                    repository, thread, turn, checkpoint_id=checkpoint_id
                )
                assert first == second
                assert await repository.load_items(thread) == after
                assert (
                    await recover_feedback(
                        repository, thread, turn, checkpoint_id="later-" + checkpoint_id
                    )
                    == ()
                )
                assert await repository.load_items(thread) == after
        finally:
            await warm.aclose()
        if remove_configuration:
            settings = replace(settings, configuration=LocalConfigState(()))
        cold = await LangGraphRuntime.acreate(
            settings=settings,
            registry=registry(),
            model=Model(),
            database_path=database,
            thread_id=thread,
            home_path=tmp_path,
        )
        try:
            events = [event async for event in cold.resume_pending()]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == (3 if yielded else 2)
            assert effects == ["once"]
            calls = tmp_path / "hook-calls"
            assert (calls.read_text().splitlines() if calls.exists() else []) == (
                [] if window == "batch" else ["once"]
            )
            result = next(i for i in requests[-1].items if isinstance(i, ToolResultItem))
            assert result.is_error == (
                False if yielded else nested or window != "completed" or action == "block"
            )
            contexts = [
                item
                for item in requests[-1].items
                if isinstance(item, ContextItem) and item.content_kind == "hooks.additional_context"
            ]
            assert [item.content for item in contexts if "recovered_block" not in item.key] == (
                ["SAVED_POST_CONTEXT"] if window == "completed" else []
            )
            items = await cold._repository.load_items(thread)
            assert items[: len(original)] == original
            assert [event async for event in cold.resume_pending()] == []
            from corki.core.post_hook_recovery import recover_feedback

            await recover_feedback(cold._repository, thread, turn)
            assert await cold._repository.load_items(thread) == items
            if action == "context":
                if not nested and window == "completed":
                    assert result.content == "ORIGINAL_RESULT"
                return
            if nested:
                # The outer JavaScript call is correctly not replayed. Its unknown
                # result must remain unknown; saved inner feedback needs a separate
                # recovery projection, not a fabricated outer return value.
                feedback = [item for item in contexts if "recovered_block" in item.key]
                assert len(feedback) == (window != "completed" or action == "block")
                assert (
                    "POST_BLOCK" if window == "completed" else "PostToolUse outcome unknown"
                ) in feedback[0].content
                assert ("Script running" if yielded else "unknown") in result.content
                assert "ORIGINAL_RESULT" not in result.content
                return
            assert (
                "POST_BLOCK" if window == "completed" else "PostToolUse outcome unknown"
            ) in result.content
            assert "ORIGINAL_RESULT" not in result.content
        finally:
            await cold.aclose()

    asyncio.run(scenario())
