"""Actual hook feedback is summarized, not resurrected as live instructions."""

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
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted, ModelError, ModelErrorKind
from corki.models.types import ModelUsage
from corki.protocol.events import ContextCompacted, TurnCompleted, TurnFailed
from corki.protocol.items import (
    AssistantMessageItem,
    CompactionItem,
    ContextItem,
    ContextRole,
    ToolResultItem,
    new_step_id,
)
from corki.protocol.tools import ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("hook_event", ["PreToolUse", "PostToolUse"])
@pytest.mark.parametrize("spill", [False, True])
@pytest.mark.parametrize(
    ("automatic", "summary_failure"), [(False, False), (False, True), (True, False)]
)
def test_hook_feedback_compaction_and_cold_history(
    tmp_path, nested, spill, automatic, summary_failure, hook_event
):
    async def scenario():
        text = "HOOK_EVIDENCE " * (2000 if spill else 1)
        command = shlex.join(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; import json; "
                "Path('hook-count').open('a').write('once\\n'); "
                f"print(json.dumps({{'hookSpecificOutput': {{'hookEventName': {hook_event!r}, "
                f"'additionalContext': {text!r}}}}}))",
            ]
        )
        source = tmp_path / "config.toml"
        event_key = "pre_tool_use" if hook_event == "PreToolUse" else "post_tool_use"
        fingerprint = command_identity(
            {"type": "command", "command": command}, event_name=hook_event, matcher="probe"
        )[0]
        document = (
            f"[[hooks.{hook_event}]]\nmatcher='probe'\n[[hooks.{hook_event}.hooks]]\n"
            f"type='command'\ncommand={json.dumps(command)}\n"
            f"[hooks.state.{json.dumps(f'{source}:{event_key}:0:0')}]\ntrusted_hash={json.dumps(fingerprint)}\n"
        )
        normal, summaries, effects = [], [], []
        fragment = None

        class Probe:
            spec = ToolSpec("probe", "test", {"type": "object"})

            async def execute(self, call, context):
                effects.append(call.id)
                return ToolResult(call.id, call.name, "TOOL_EVIDENCE")

        class Model:
            async def stream(self, request):
                nonlocal fragment
                contexts = [
                    item
                    for item in request.items
                    if isinstance(item, ContextItem)
                    and item.content_kind == "hooks.additional_context"
                ]
                if not request.tools:
                    summaries.append(request)
                    assert len(contexts) == 1
                    if fragment is None:
                        fragment = contexts[0]
                    assert contexts == [fragment]
                    assert fragment.role == ContextRole.DEVELOPER
                    assert any(isinstance(item, ToolResultItem) for item in request.items)
                    if summary_failure and len(summaries) <= 2:
                        raise ModelError("summary denied", kind=ModelErrorKind.AUTHENTICATION)
                    yield ModelCompleted(
                        (
                            AssistantMessageItem(
                                "SAVED_HOOK_AND_TOOL_PROGRESS",
                                request.items[-1].turn_id,
                                new_step_id(),
                            ),
                        )
                    )
                    return
                normal.append(request)
                if len(normal) == 1:
                    event = request_call(
                        request,
                        "exec" if nested else "probe",
                        "text(await tools.probe({}));" if nested else {},
                    )
                    yield (
                        replace(event, usage=ModelUsage(input_tokens=60000, output_tokens=10))
                        if automatic
                        else event
                    )
                    return
                if len(normal) == 2 and not automatic:
                    assert len(contexts) == 1
                    fragment = contexts[0]
                    assert fragment.role == ContextRole.DEVELOPER
                else:
                    assert not contexts
                    assert any(isinstance(item, CompactionItem) for item in request.items)
                    assert "SAVED_HOOK_AND_TOOL_PROGRESS" in repr(request.items)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        async def create(thread=None):
            registry = ToolRegistry()
            registry.register(Probe())
            return await LangGraphRuntime.acreate(
                settings=CorkiSettings(
                    tmp_path,
                    skills_enabled=False,
                    plugins_enabled=False,
                    context_window_tokens=100000,
                    auto_compact_tokens=50000,
                    model_max_retries=1,
                    model_retry_base_seconds=0.001,
                    tool_mode="code_mode_only" if nested else "direct",
                    configuration=LocalConfigState(
                        (ConfigLayer(source, "user", contents=document),)
                    ),
                ),
                registry=registry,
                model=Model(),
                database_path=tmp_path / "state.db",
                home_path=tmp_path,
                thread_id=thread,
            )

        warm = await create()
        try:
            events = [event async for event in warm.stream("use probe")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            thread = warm.thread_id
            original = await warm._repository.load_items(thread)
            if not automatic:
                events = [event async for event in warm.compact()]
                if summary_failure:
                    assert isinstance(events[-1], TurnFailed), events[-1]
                    assert await warm._repository.load_items(thread) == original
                    events = [event async for event in warm.compact()]
                assert isinstance(events[-1], TurnCompleted), events[-1]
            assert any(isinstance(event, ContextCompacted) for event in events)
            assert len(summaries) == (3 if summary_failure else 1)
            stored = await warm._repository.load_items(thread)
            assert stored[: len(original)] == original
        finally:
            await warm.aclose()
        cold = await create(thread)
        try:
            from corki.core.pre_hook_recovery import recover_feedback

            class NoWarnings:
                async def emit(self, event):
                    raise AssertionError("an archived fragment needs no reconstruction")

            if hook_event == "PreToolUse":
                await recover_feedback(cold._repository, thread, fragment.turn_id, NoWarnings())
            else:
                from corki.core.post_hook_recovery import recover_feedback as recover_post

                assert (
                    await recover_post(
                        cold._repository,
                        thread,
                        fragment.turn_id,
                        checkpoint_id="after-installed-compaction",
                    )
                    == ()
                )
            assert await cold._repository.load_items(thread) == stored
            events = [event async for event in cold.stream("continue")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(normal) == 3 and len(effects) == 1
            assert (tmp_path / "hook-count").read_text().splitlines() == ["once"]
            archived = await cold._repository.load_items(thread)
            assert archived[: len(stored)] == stored
            assert sum(item.id == fragment.id for item in archived) == 1
        finally:
            await cold.aclose()

    asyncio.run(scenario())
