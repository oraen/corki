"""Post commands run after committed side effects, with separate model feedback."""

import asyncio
import json
import shlex
import sys

import pytest
from test_code_mode import request_call

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.post_tool_hooks import run as run_post_hooks
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import HookCompleted, ToolCallCompleted, TurnCompleted
from corki.protocol.items import ContextItem, ToolResultItem
from corki.protocol.tools import ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize(
    "action",
    [
        "block",
        "context",
        "stopped",
        "stopped_control",
        "stopped_empty",
        "invalid_wire",
        "tool_error",
        "hook_error",
        "untrusted",
    ],
)
def test_post_feedback_preserves_actual_tool_fact(tmp_path, nested, action):
    async def scenario():
        stopped = action.startswith("stopped")
        expected_feedback = "" if action == "stopped_empty" else "POST_FEEDBACK"
        output = {"decision": "block", "reason": "POST_FEEDBACK"}
        if action == "context":
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": "POST_CONTEXT",
                }
            }
        elif stopped:
            output = {"continue": False, "stopReason": expected_feedback}
            if action == "stopped_control":
                output["suppressOutput"] = True
        elif action == "invalid_wire":
            output["suppressOutput"] = 0
        command = shlex.join(
            [
                sys.executable,
                "-c",
                "import json,sys; from pathlib import Path; p=json.load(sys.stdin); "
                "assert Path('effect.txt').read_text()=='original effect'; "
                "Path('post-input.json').write_text(json.dumps(p)); "
                "Path('post-count').open('a').write('run\\n'); "
                f"print(json.dumps({output!r})); sys.exit({1 if action == 'hook_error' else 0})",
            ]
        )
        source = tmp_path / "config.toml"
        fingerprint = command_identity(
            {"type": "command", "command": command}, event_name="PostToolUse", matcher="probe"
        )[0]
        definition = (
            "[[hooks.PostToolUse]]\nmatcher='probe'\n"
            f"[[hooks.PostToolUse.hooks]]\ntype='command'\ncommand={json.dumps(command)}\n"
        )
        if action != "untrusted":
            definition += (
                f"[hooks.state.{json.dumps(f'{source}:post_tool_use:0:0')}]\n"
                f"trusted_hash={json.dumps(fingerprint)}\n"
            )
        effects, requests = [], []

        class Probe:
            spec = ToolSpec("probe", "fixture", {"type": "object"})

            async def execute(self, call, context):
                effects.append(call.arguments)
                (tmp_path / "effect.txt").write_text("original effect")
                return ToolResult(
                    call.id, call.name, "ORIGINAL_RESULT", is_error=action == "tool_error"
                )

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    yield request_call(
                        request,
                        "exec" if nested else "probe",
                        'text(await tools.probe({value:"input"}))'
                        if nested
                        else {"value": "input"},
                    )
                else:
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Probe())
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
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
        try:
            events = [event async for event in runtime.stream("do tool")]
            assert isinstance(events[-1], TurnCompleted)
            assert effects == [{"value": "input"}]
            outcomes = await runtime._repository.load_turn_tool_outcomes(
                runtime.thread_id, events[-1].turn_id
            )
            original = next(result for result in outcomes if result.tool_name == "probe")
            assert original.content == "ORIGINAL_RESULT"
            assert original.is_error == (action == "tool_error")
            result = next(
                item for item in reversed(requests[-1].items) if isinstance(item, ToolResultItem)
            )
            if action == "block" or (stopped and not nested):
                assert (
                    expected_feedback in result.content and "ORIGINAL_RESULT" not in result.content
                )
            else:
                assert "ORIGINAL_RESULT" in result.content
            if action in {"untrusted", "tool_error"}:
                assert not (tmp_path / "post-input.json").exists()
            else:
                payload = json.loads((tmp_path / "post-input.json").read_text())
                assert payload["tool_input"] == {"value": "input"}
                assert payload["tool_response"] == "ORIGINAL_RESULT"
                assert sum(isinstance(event, HookCompleted) for event in events) == 1
                hook_index = next(
                    i for i, event in enumerate(events) if isinstance(event, HookCompleted)
                )
                tool_index = next(
                    i
                    for i, event in enumerate(events)
                    if isinstance(event, ToolCallCompleted) and event.tool_name == "probe"
                )
                assert hook_index < tool_index
                if stopped:
                    assert events[hook_index].run.status == "stopped"
                    assert any(
                        entry.kind == "stop" and entry.text == expected_feedback
                        for entry in events[hook_index].run.entries
                    )
                if action == "invalid_wire":
                    assert events[hook_index].run.status == "failed"
            contexts = [
                item
                for item in requests[-1].items
                if isinstance(item, ContextItem) and item.content_kind == "hooks.additional_context"
            ]
            assert [item.content for item in contexts] == (
                ["POST_CONTEXT"] if action == "context" else []
            )
            before = await runtime._repository.load_items(runtime.thread_id)
            recovered = await run_post_hooks(
                ((), ()),
                result=original,
                tool=None,
                state={
                    "thread_id": runtime.thread_id,
                    "turn_id": events[-1].turn_id,
                    "request_items": requests[-1].items,
                },
                runtime=None,
                settings=None,
                repository=runtime._repository,
                shell=None,
                environment={},
                fresh=False,
                nested=nested,
            )
            assert await runtime._repository.load_items(runtime.thread_id) == before
            assert recovered.content == (
                expected_feedback
                if action == "block" or (stopped and not nested)
                else "ORIGINAL_RESULT"
            )
            assert recovered.is_error == (action in {"block", "tool_error"})
            if action not in {"untrusted", "tool_error"}:
                assert (tmp_path / "post-count").read_text() == "run\n"
            from corki.core.post_hook_recovery import recover_feedback

            await recover_feedback(runtime._repository, runtime.thread_id, events[-1].turn_id)
            assert await runtime._repository.load_items(runtime.thread_id) == before
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
