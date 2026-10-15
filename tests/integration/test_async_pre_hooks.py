"""Async Pre commands provide context without rewriting admitted tool input."""

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
from corki.protocol.events import HookCompleted, HookStarted, TurnCompleted, TurnFailed
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ContextItem,
    ToolResultItem,
    new_step_id,
)
from corki.protocol.tools import ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize(
    "delivery",
    [
        "same_turn",
        "cold_completed",
        "cold_unknown",
        "cold_after_append",
        "cold_before_receipt",
        "cold_after_receipt",
        "cold_compacted",
        "cold_auto_compacted",
        "cold_compaction_retry",
    ],
)
def test_async_pre_executes_without_waiting_or_rewriting(tmp_path, nested, delivery):
    async def scenario():
        command = shlex.join(
            [
                sys.executable,
                "-c",
                (
                    "import json,os,sys,time; from pathlib import Path; p=json.load(sys.stdin); "
                    "assert p['tool_input']=={'value':'original'}; "
                    "assert 'tool_response' not in p; "
                    "Path('started').write_text(str(os.getpid())); "
                    "Path('count').open('a').write('once\\n'); "
                    "exec(\"while not Path('release').exists(): time.sleep(0.01)\"); "
                    "print(json.dumps({'hookSpecificOutput':{'hookEventName':'PreToolUse',"
                    "'permissionDecision':'allow','updatedInput':{'value':'changed'},"
                    "'additionalContext':'ASYNC_PRE_CONTEXT'}}))"
                ),
            ]
        )
        fingerprint, _ = command_identity(
            {"type": "command", "command": command, "async": True},
            event_name="PreToolUse",
            matcher="probe",
        )
        source = tmp_path / "config.toml"
        definition = (
            "[[hooks.PreToolUse]]\nmatcher='probe'\n[[hooks.PreToolUse.hooks]]\n"
            f"type='command'\nasync=true\ncommand={json.dumps(command)}\n"
            f"[hooks.state.{json.dumps(f'{source}:pre_tool_use:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        requests, effects, summaries = [], [], []
        committed = asyncio.Event()

        class Probe:
            spec = ToolSpec("probe", "fixture", {"type": "object"})

            async def execute(self, call, context):
                assert not (tmp_path / "release").exists()
                effects.append(call.arguments)
                return ToolResult(call.id, call.name, "original result")

        class Model:
            async def stream(self, request):
                if not request.tools:
                    summaries.append(request)
                    assert (
                        sum(
                            isinstance(item, ContextItem) and item.content == "ASYNC_PRE_CONTEXT"
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
                                "ASYNC_PRE_SUMMARY", request.items[-1].turn_id, new_step_id()
                            ),
                        )
                    )
                    return
                requests.append(request)
                if len(requests) == 1:
                    yield request_call(
                        request,
                        "exec" if nested else "probe",
                        'text(await tools.probe({value:"original"}))'
                        if nested
                        else {"value": "original"},
                    )
                    return
                if len(requests) == 2:
                    async with asyncio.timeout(3):
                        while not (tmp_path / "started").exists():
                            await asyncio.sleep(0.01)
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
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )
        complete = runtime._repository.complete_hook_execution

        async def observed(thread, turn, key, request, result):
            await complete(thread, turn, key, request, result)
            if key.startswith("pre_tool_use:"):
                committed.set()

        runtime._repository.complete_hook_execution = observed
        try:
            async with asyncio.timeout(10):
                events = [event async for event in runtime.stream("probe")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert effects == [{"value": "original"}]
            if delivery != "same_turn":
                assert len(requests) == 2
                assert not committed.is_set()
                if delivery != "cold_unknown":
                    publish = await runtime._prepare_configuration_reload(LocalConfigState(()), {})
                    publish()
                    (tmp_path / "release").touch()
                    async with asyncio.timeout(3):
                        await committed.wait()
                thread_id = runtime.thread_id
                original_turn = events[-1].turn_id
                if delivery in {"cold_after_append", "cold_before_receipt", "cold_after_receipt"}:
                    append = runtime._repository.append_items
                    save = runtime._repository.save_hook_batch
                    injected = False

                    async def interrupted_append(thread, items):
                        nonlocal injected
                        await append(thread, items)
                        if not injected and any(
                            isinstance(item, ContextItem) and item.content == "ASYNC_PRE_CONTEXT"
                            for item in items
                        ):
                            injected = True
                            raise OSError("injected after Pre context commit")

                    async def interrupted_receipt(thread, turn, key, value):
                        nonlocal injected
                        if key.startswith("async_pre_delivered:") and not injected:
                            injected = True
                            if delivery == "cold_after_receipt":
                                await save(thread, turn, key, value)
                            raise OSError("injected at Pre receipt commit")
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
                owner = runtime._graph._async_pre_hooks.owner
                async with asyncio.timeout(5):
                    await runtime.aclose()
                assert not owner._tasks
                if delivery == "cold_unknown":
                    assert not committed.is_set()
                    with pytest.raises(ProcessLookupError):
                        os.kill(int((tmp_path / "started").read_text()), 0)
                    (tmp_path / "release").touch()

                async def reopen():
                    registry = ToolRegistry()
                    registry.register(Probe())
                    return await LangGraphRuntime.acreate(
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
                        registry=registry,
                        model=Model(),
                        database_path=tmp_path / "state.db",
                        home_path=tmp_path,
                        thread_id=thread_id,
                    )

                runtime = await reopen()
                events.extend([event async for event in runtime.stream("follow up")])
                assert isinstance(events[-1], TurnCompleted), events[-1]
                records = await runtime._repository.load_hook_executions(
                    thread_id, original_turn, "pre_tool_use:"
                )
                assert len(records) == 1
                assert (records[0][2] is None) == (delivery == "cold_unknown")
            assert len(requests) == 3
            assert [
                item.content
                for item in requests[-1].items
                if isinstance(item, ContextItem) and item.content_kind == "hooks.additional_context"
            ] == ([] if delivery == "cold_unknown" else ["ASYNC_PRE_CONTEXT"])
            if delivery != "same_turn":
                original = await runtime._repository.load_items(thread_id)
                if delivery in {"cold_compacted", "cold_compaction_retry"}:
                    compacted = [event async for event in runtime.compact()]
                    if delivery == "cold_compaction_retry":
                        assert isinstance(compacted[-1], TurnFailed), compacted[-1]
                        assert await runtime._repository.load_items(thread_id) == original
                        compacted = [event async for event in runtime.compact()]
                    assert isinstance(compacted[-1], TurnCompleted), compacted[-1]
                await runtime.aclose()
                runtime = await reopen()
                events.extend([event async for event in runtime.stream("second reopening")])
                assert isinstance(events[-1], TurnCompleted), events[-1]
                assert len(requests) == 4
                history = await runtime._repository.load_items(thread_id)
                assert history[: len(original)] == original
                assert [
                    item.content
                    for item in history
                    if isinstance(item, ContextItem)
                    and item.content_kind == "hooks.additional_context"
                ] == ([] if delivery == "cold_unknown" else ["ASYNC_PRE_CONTEXT"])
                if delivery in {"cold_compacted", "cold_auto_compacted", "cold_compaction_retry"}:
                    assert len(summaries) == (2 if delivery == "cold_compaction_retry" else 1)
                    assert any(isinstance(item, CompactionItem) for item in requests[-1].items)
                    assert "ASYNC_PRE_SUMMARY" in repr(requests[-1].items)
                    assert not any(
                        isinstance(item, ContextItem) and item.content == "ASYNC_PRE_CONTEXT"
                        for item in requests[-1].items
                    )
                assert effects == [{"value": "original"}]
            assert (tmp_path / "count").read_text() == "once\n"
            assert not any(isinstance(event, (HookStarted, HookCompleted)) for event in events)
        finally:
            (tmp_path / "release").touch()
            await runtime.aclose()

    asyncio.run(scenario())
