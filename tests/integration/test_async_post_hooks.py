"""Background Post feedback must cross a sampling boundary without controlling tools."""

import asyncio
import json
import os
import shlex
import sys

import pytest
from test_code_mode import request_call

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted, ModelError, ModelErrorKind
from corki.models.types import ModelUsage
from corki.protocol.events import (
    HookCompleted,
    HookStarted,
    TurnCompleted,
    TurnFailed,
    WarningEvent,
)
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ContextItem,
    ToolResultItem,
    UserMessageItem,
    new_step_id,
)
from corki.protocol.tools import ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("control", ["block", "stop", "unsupported"])
@pytest.mark.parametrize(
    "delivery",
    [
        "same_turn",
        "next_turn",
        "cold_next_turn",
        "cold_after_append",
        "cold_before_receipt",
        "cold_after_receipt",
        "cold_unknown",
        "cold_compacted",
        "cold_auto_compacted",
        "cold_compaction_retry",
    ],
)
def test_async_post_feedback_after_last_sample(tmp_path, nested, control, delivery):
    async def scenario():
        output = {
            "systemMessage": "ASYNC_POST_WARNING",
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": "ASYNC_POST_CONTEXT",
            },
        }
        if control == "block":
            output.update(decision="block", reason="DO_NOT_BLOCK")
        elif control == "stop":
            output.update({"continue": False, "stopReason": "DO_NOT_STOP"})
        else:
            output["suppressOutput"] = True
            output["hookSpecificOutput"]["updatedMCPToolOutput"] = {"ignored": True}
        script = (
            "import json,os,sys,time; from pathlib import Path; p=json.load(sys.stdin); "
            "assert p['tool_response']=='ORIGINAL_RESULT'; "
            "Path('hook-started').write_text(str(os.getpid())); "
            "Path('hook-count').open('a').write('run\\n'); "
            "exec(\"while not Path('release').exists(): time.sleep(0.01)\"); "
            f"print(json.dumps({output!r}))"
        )
        command = shlex.join([sys.executable, "-c", script])
        handler = {"type": "command", "command": command, "async": True}
        fingerprint, _ = command_identity(handler, event_name="PostToolUse", matcher="probe")
        source = tmp_path / "config.toml"
        definition = (
            "[[hooks.PostToolUse]]\nmatcher='probe'\n"
            "[[hooks.PostToolUse.hooks]]\ntype='command'\nasync=true\n"
            f"command={json.dumps(command)}\n"
            f"[hooks.state.{json.dumps(f'{source}:post_tool_use:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        requests, effects, summaries = [], [], []
        committed = asyncio.Event()

        class Probe:
            spec = ToolSpec("probe", "fixture", {"type": "object"})

            async def execute(self, call, context):
                effects.append(call.id)
                return ToolResult(call.id, call.name, "ORIGINAL_RESULT")

        class Model:
            async def stream(self, request):
                if not request.tools:
                    summaries.append(request)
                    assert (
                        sum(
                            isinstance(item, ContextItem) and item.content == "ASYNC_POST_CONTEXT"
                            for item in request.items
                        )
                        == 1
                    )
                    assert any(isinstance(item, ToolResultItem) for item in request.items)
                    if delivery == "cold_compaction_retry" and len(summaries) == 1:
                        raise ModelError("summary denied", kind=ModelErrorKind.AUTHENTICATION)
                    yield ModelCompleted(
                        (
                            AssistantMessageItem(
                                "ASYNC_HOOK_SUMMARY", request.items[-1].turn_id, new_step_id()
                            ),
                        )
                    )
                    return
                requests.append(request)
                if len(requests) == 1:
                    yield request_call(
                        request,
                        "exec" if nested else "probe",
                        "text(await tools.probe({}))" if nested else {},
                    )
                    return
                if len(requests) == 2:
                    # Reaching this sample before release proves the tool did not
                    # wait for the background command. Wait on actual process
                    # admission, then its durable result, not an arbitrary delay.
                    async with asyncio.timeout(3):
                        while not (tmp_path / "hook-started").exists():
                            await asyncio.sleep(0.01)
                    assert not committed.is_set()
                    if delivery == "same_turn":
                        (tmp_path / "release").touch()
                        async with asyncio.timeout(3):
                            await committed.wait()
                yield ModelCompleted(
                    (),
                    usage=ModelUsage(input_tokens=60000, output_tokens=10)
                    if delivery == "cold_auto_compacted" and len(requests) == 3
                    else ModelUsage(),
                )

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Probe())
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                context_window_tokens=100000,
                auto_compact_tokens=50000,
                model_max_retries=0,
                skills_enabled=False,
                plugins_enabled=False,
                tool_mode="code_mode_only" if nested else "direct",
                configuration=LocalConfigState((ConfigLayer(source, "user", contents=definition),)),
            ),
            registry=registry,
            model=Model(),
            home_path=tmp_path,
            database_path=tmp_path / "state.db",
        )
        complete = runtime._repository.complete_hook_execution

        async def observed_commit(thread, turn, key, request, result):
            await complete(thread, turn, key, request, result)
            if key.startswith("post_tool_use:"):
                committed.set()

        runtime._repository.complete_hook_execution = observed_commit
        try:
            async with asyncio.timeout(10):
                events = [event async for event in runtime.stream("do tool")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            if delivery != "same_turn":
                assert len(requests) == 2
                assert not committed.is_set()
                if delivery != "cold_unknown":
                    publish = await runtime._prepare_configuration_reload(LocalConfigState(()), {})
                    publish()
                    (tmp_path / "release").touch()
                    async with asyncio.timeout(3):
                        await committed.wait()
                if delivery in {"cold_after_append", "cold_before_receipt", "cold_after_receipt"}:
                    append = runtime._repository.append_items
                    save = runtime._repository.save_hook_batch
                    injected = False

                    async def interrupted_append(thread, items):
                        nonlocal injected
                        await append(thread, items)
                        if not injected and any(
                            isinstance(item, ContextItem) and item.content == "ASYNC_POST_CONTEXT"
                            for item in items
                        ):
                            injected = True
                            raise OSError("injected after context commit")

                    async def interrupted_receipt(thread, turn, key, value):
                        nonlocal injected
                        if key.startswith("async_post_delivered:") and not injected:
                            injected = True
                            if delivery == "cold_after_receipt":
                                await save(thread, turn, key, value)
                            raise OSError("injected at receipt commit")
                        await save(thread, turn, key, value)

                    if delivery == "cold_after_append":
                        runtime._repository.append_items = interrupted_append
                    else:
                        runtime._repository.save_hook_batch = interrupted_receipt
                    interrupted = [event async for event in runtime.stream("interrupted delivery")]
                    assert injected
                    assert isinstance(interrupted[-1], TurnFailed), interrupted[-1]
                    assert len(requests) == 2
                    events.extend(interrupted)
                if delivery.startswith("cold_"):
                    thread_id = runtime.thread_id
                    owner = runtime._graph._async_post_hooks.owner
                    async with asyncio.timeout(5):
                        await runtime.aclose()
                    assert not owner._tasks
                    if delivery == "cold_unknown":
                        assert not committed.is_set()
                        pid = int((tmp_path / "hook-started").read_text())
                        with pytest.raises(ProcessLookupError):
                            os.kill(pid, 0)
                        # Keep the trusted hook configured and allow a mistaken
                        # replay to finish, so the count check cannot hide it.
                        (tmp_path / "release").touch()
                    cold_registry = ToolRegistry()
                    cold_registry.register(Probe())
                    runtime = await LangGraphRuntime.acreate(
                        settings=CorkiSettings(
                            tmp_path,
                            context_window_tokens=100000,
                            auto_compact_tokens=50000,
                            model_max_retries=0,
                            skills_enabled=False,
                            plugins_enabled=False,
                            tool_mode="code_mode_only" if nested else "direct",
                            configuration=LocalConfigState(
                                (ConfigLayer(source, "user", contents=definition),)
                                if delivery == "cold_unknown"
                                else ()
                            ),
                        ),
                        registry=cold_registry,
                        model=Model(),
                        home_path=tmp_path,
                        database_path=tmp_path / "state.db",
                        thread_id=thread_id,
                    )
                events.extend([event async for event in runtime.stream("follow up")])
                assert isinstance(events[-1], TurnCompleted), events[-1]
                if delivery == "cold_unknown":
                    original_turn = next(
                        event.turn_id for event in events if isinstance(event, TurnCompleted)
                    )
                    records = await runtime._repository.load_hook_executions(
                        runtime.thread_id, original_turn, "post_tool_use:"
                    )
                    assert len(records) == 1 and records[0][2] is None
                items = requests[-1].items
                context_indices = [
                    i
                    for i, item in enumerate(items)
                    if isinstance(item, ContextItem) and item.content == "ASYNC_POST_CONTEXT"
                ]
                user_index = next(
                    i
                    for i, item in enumerate(items)
                    if isinstance(item, UserMessageItem) and item.content == "follow up"
                )
                if delivery == "cold_unknown":
                    assert context_indices == []
                else:
                    assert len(context_indices) == 1 and context_indices[0] < user_index
            assert len(requests) == 3
            assert len(effects) == 1
            assert (tmp_path / "hook-count").read_text() == "run\n"
            contexts = [
                item.content
                for item in requests[-1].items
                if isinstance(item, ContextItem) and item.content_kind == "hooks.additional_context"
            ]
            assert contexts == ([] if delivery == "cold_unknown" else ["ASYNC_POST_CONTEXT"])
            results = [item for item in requests[-1].items if isinstance(item, ToolResultItem)]
            assert results and "ORIGINAL_RESULT" in results[-1].content
            assert not results[-1].is_error
            assert not any(isinstance(event, (HookStarted, HookCompleted)) for event in events)
            warnings = [
                event
                for event in events
                if isinstance(event, WarningEvent) and event.message == "ASYNC_POST_WARNING"
            ]
            # Warning delivery may repeat across an emit -> receipt crash;
            # async hooks never produce synchronous lifecycle notifications.
            assert len(warnings) == (
                0 if delivery == "cold_unknown" else 2 if delivery == "cold_before_receipt" else 1
            )
            original_turn = next(
                event.turn_id for event in events if isinstance(event, TurnCompleted)
            )
            assert all(event.thread_id == runtime.thread_id for event in warnings)
            assert all(
                (event.turn_id == original_turn) == (delivery == "same_turn") for event in warnings
            )
            if delivery.startswith("cold_"):
                if delivery in {"cold_compacted", "cold_compaction_retry"}:
                    original = await runtime._repository.load_items(runtime.thread_id)
                    compacted = [event async for event in runtime.compact()]
                    if delivery == "cold_compaction_retry":
                        assert isinstance(compacted[-1], TurnFailed), compacted[-1]
                        assert await runtime._repository.load_items(runtime.thread_id) == original
                        compacted = [event async for event in runtime.compact()]
                    assert isinstance(compacted[-1], TurnCompleted), compacted[-1]
                    assert len(summaries) == (2 if delivery == "cold_compaction_retry" else 1)
                    stored = await runtime._repository.load_items(runtime.thread_id)
                    assert stored[: len(original)] == original
                thread_id = runtime.thread_id
                settings = runtime._settings
                await runtime.aclose()
                registry = ToolRegistry()
                registry.register(Probe())
                runtime = await LangGraphRuntime.acreate(
                    settings=settings,
                    registry=registry,
                    model=Model(),
                    home_path=tmp_path,
                    database_path=tmp_path / "state.db",
                    thread_id=thread_id,
                )
                again = [event async for event in runtime.stream("after delivery")]
                assert isinstance(again[-1], TurnCompleted), again[-1]
                assert not any(isinstance(event, HookCompleted) for event in again)
                assert len(effects) == 1
                assert (tmp_path / "hook-count").read_text() == "run\n"
                assert sum(
                    isinstance(item, ContextItem) and item.content == "ASYNC_POST_CONTEXT"
                    for item in requests[-1].items
                ) == (
                    0
                    if delivery
                    in {
                        "cold_unknown",
                        "cold_compacted",
                        "cold_auto_compacted",
                        "cold_compaction_retry",
                    }
                    else 1
                )
                if delivery in {"cold_compacted", "cold_auto_compacted", "cold_compaction_retry"}:
                    assert len(summaries) == (2 if delivery == "cold_compaction_retry" else 1)
                    assert any(isinstance(item, CompactionItem) for item in requests[-1].items)
                    assert "ASYNC_HOOK_SUMMARY" in repr(requests[-1].items)
        finally:
            (tmp_path / "release").touch()
            await runtime.aclose()

    asyncio.run(scenario())
