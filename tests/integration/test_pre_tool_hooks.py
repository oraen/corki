"""Trusted command PreToolUse effects reach direct and nested Runtime dispatch."""

import asyncio
import json
import shlex
import sys
from pathlib import Path

import pytest
from test_code_mode import request_call

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import HookCompleted, TurnCompleted
from corki.protocol.ids import ThreadId
from corki.protocol.items import ContextItem, ContextRole, ToolCallItem, ToolResultItem
from corki.protocol.session_source import (
    SessionSource,
    SessionSourceKind,
    SubAgentSource,
    ThreadSpawnSource,
)
from corki.protocol.tools import ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize(
    "action",
    [
        "block",
        "rewrite",
        "invalid_schema",
        "invalid_output",
        "untrusted",
        "competing",
        "transcript_error",
        "warning",
        "warning_invalid_control",
        "invalid_warning",
        "context",
        "context_block",
        "context_invalid",
        "context_bad_type",
        "context_spill",
        "context_spill_error",
        "context_zero",
        "child_default",
        "child_named",
        "child_empty",
        "internal",
        "custom_label",
        "spawn_alias",
        "spawn_alias_union",
        "patch_imposter",
        "stdin_imposter",
        "handler_mutation",
    ],
)
def test_trusted_pre_hook_controls_actual_handler_without_rewriting_history(
    tmp_path, monkeypatch, nested, action
):
    async def scenario():
        tool_name = (
            "spawn_agent"
            if action.startswith("spawn_alias")
            else "apply_patch"
            if action == "patch_imposter"
            else "write_stdin"
            if action == "stdin_imposter"
            else "probe"
        )
        matcher = (
            "Agent|spawn_agent"
            if action == "spawn_alias_union"
            else "Agent"
            if action == "spawn_alias"
            else tool_name
        )
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": {"value": "rewritten"},
            }
        }
        if action == "block":
            output = {"decision": "block", "reason": "policy denied"}
        elif action == "invalid_schema":
            output["hookSpecificOutput"]["updatedInput"]["value"] = 123
        elif action == "invalid_output":
            output = {"hookSpecificOutput": {"permissionDecision": "allow"}}
        elif action in {"warning", "warning_invalid_control", "invalid_warning"}:
            output["systemMessage"] = 123 if action == "invalid_warning" else "hook notice " * 500
            if action == "warning_invalid_control":
                output["continue"] = False
        context_text = "hook context 中文 " * (2000 if action.startswith("context_") else 1)
        if action.startswith("context"):
            output["hookSpecificOutput"]["additionalContext"] = context_text
            if action == "context_block":
                output["hookSpecificOutput"].update(
                    permissionDecision="deny", permissionDecisionReason="policy denied"
                )
                del output["hookSpecificOutput"]["updatedInput"]
            elif action == "context_invalid":
                output["continue"] = False
            elif action == "context_bad_type":
                output["hookSpecificOutput"]["additionalContext"] = 123
        command = shlex.join(
            [
                sys.executable,
                "-c",
                "import json,sys; from pathlib import Path; "
                "p=json.load(sys.stdin); Path('hook-input.json').write_text(json.dumps(p)); "
                "Path('hook-transcript.jsonl').write_text("
                "Path(p['transcript_path']).read_text() "
                "if p['transcript_path'] else 'unavailable'); "
                f"print(json.dumps({output!r}))",
            ]
        )
        source = tmp_path / "config.toml"
        handler = {"type": "command", "command": command}
        if action == "context_zero":
            handler["additionalContextLimit"] = 0
        fingerprint = command_identity(handler, event_name="PreToolUse", matcher=matcher)[0]
        definition = (
            f"[[hooks.PreToolUse]]\nmatcher={json.dumps(matcher)}\n"
            f"[[hooks.PreToolUse.hooks]]\ntype='command'\ncommand={json.dumps(command)}\n"
        )
        if action == "context_zero":
            definition += "additionalContextLimit=0\n"
        if action != "untrusted":
            definition += (
                f"[hooks.state.{json.dumps(f'{source}:pre_tool_use:0:0')}]\n"
                f"trusted_hash={json.dumps(fingerprint)}\n"
            )
        if action == "competing":
            second_command = command.replace("rewritten", "last")
            second_hash = command_identity(
                {"type": "command", "command": second_command},
                event_name="PreToolUse",
                matcher="probe",
            )[0]
            definition += (
                "[[hooks.PreToolUse.hooks]]\ntype='command'\n"
                f"command={json.dumps(second_command)}\n"
                f"[hooks.state.{json.dumps(f'{source}:pre_tool_use:0:1')}]\n"
                f"trusted_hash={json.dumps(second_hash)}\n"
            )
        effects, requests = [], []

        class Probe:
            spec = ToolSpec(
                tool_name,
                "test",
                {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
            )

            async def execute(self, call, context):
                assert context.is_non_root_agent == provenance.is_non_root_agent
                assert context.request_user_input is not None
                effects.append(call.arguments["value"])
                if action == "handler_mutation":
                    call.arguments["value"] = "handler-mutated"
                return ToolResult(call.id, call.name, call.arguments["value"])

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    yield request_call(
                        request,
                        "exec" if nested else tool_name,
                        f'text(await tools.{tool_name}({{value:"original"}}));'
                        if nested
                        else {"value": "original"},
                    )
                else:
                    result = next(
                        i for i in reversed(request.items) if isinstance(i, ToolResultItem)
                    )
                    assert result.is_error == (
                        action in {"block", "invalid_schema", "context_block"}
                    )
                    if action.startswith("context"):
                        fragments = [
                            i
                            for i in request.items
                            if isinstance(i, ContextItem)
                            and i.content_kind == "hooks.additional_context"
                        ]
                        if action in {"context_invalid", "context_bad_type"}:
                            assert not fragments
                        else:
                            assert len(fragments) == 1
                            fragment = fragments[0]
                            assert fragment.role == ContextRole.DEVELOPER
                            assert fragment.source_input_id is not None
                            if action in {"context", "context_zero"}:
                                assert fragment.content == context_text
                            else:
                                assert len(fragment.content.encode()) < 11000
                                assert "truncated output" in fragment.content
                                if action != "context_spill_error":
                                    path = Path(
                                        fragment.content.split("Full hook output saved to: ")[1]
                                    )
                                    assert path.read_text() == context_text
                                    assert path.stat().st_mode & 0o077 == 0
                                else:
                                    assert "Full hook output saved to:" not in fragment.content
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Probe())
        provenance = SessionSource()
        if action.startswith("child_"):
            provenance = SessionSource.subagent(
                SubAgentSource(
                    "thread_spawn",
                    ThreadSpawnSource(
                        ThreadId("parent-thread"),
                        1,
                        agent_role="reviewer"
                        if action == "child_named"
                        else ""
                        if action == "child_empty"
                        else None,
                    ),
                )
            )
        elif action == "internal":
            provenance = SessionSource(SessionSourceKind.INTERNAL, "memory_consolidation")
        elif action == "custom_label":
            provenance = SessionSource(SessionSourceKind.CUSTOM, "subagent:thread_spawn")
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
            session_source=provenance,
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )
        if action == "competing":
            import corki.core.pre_tool_hooks as hooks

            saving_first, saved_second = asyncio.Event(), asyncio.Event()
            run, complete = hooks.run_command, runtime._repository.complete_hook_execution

            async def ordered_run(hook, *args, **kwargs):
                if hook.command == second_command:
                    await asyncio.wait_for(saving_first.wait(), 3)
                return await run(hook, *args, **kwargs)

            async def delayed_commit(thread, turn, key, request, result):
                if request["command"] == command:
                    saving_first.set()
                    await asyncio.wait_for(saved_second.wait(), 3)
                await complete(thread, turn, key, request, result)
                if request["command"] == second_command:
                    saved_second.set()

            monkeypatch.setattr(hooks, "run_command", ordered_run)
            monkeypatch.setattr(runtime._repository, "complete_hook_execution", delayed_commit)
        if action == "transcript_error":

            async def unavailable(thread_id):
                raise OSError("transcript publication unavailable")

            monkeypatch.setattr(runtime._repository, "materialize_transcript", unavailable)
        if action == "context_spill_error":
            import corki.core.hook_context as feedback

            def unavailable_output(**kwargs):
                raise OSError("hook output storage unavailable")

            monkeypatch.setattr(feedback.tempfile, "mkstemp", unavailable_output)
        if action.startswith("context"):
            from corki.core.runtime import _QueueEventSink

            completed_runs = []
            emit, append = _QueueEventSink.emit, runtime._repository.append_items

            async def observe_completed(sink, event):
                if isinstance(event, HookCompleted):
                    completed_runs.append(event.run)
                await emit(sink, event)

            async def append_after_completed(thread, items):
                if any(
                    isinstance(item, ContextItem)
                    and item.content_kind == "hooks.additional_context"
                    for item in items
                ):
                    assert len(completed_runs) == 1
                await append(thread, items)

            monkeypatch.setattr(_QueueEventSink, "emit", observe_completed)
            monkeypatch.setattr(runtime._repository, "append_items", append_after_completed)
        try:
            events = [event async for event in runtime.stream("use probe")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 2
            assert effects == (
                []
                if action in {"block", "invalid_schema", "context_block"}
                else [
                    "last"
                    if action == "competing"
                    else "rewritten"
                    if action
                    in {
                        "rewrite",
                        "transcript_error",
                        "warning",
                        "context",
                        "context_spill",
                        "context_spill_error",
                        "context_zero",
                        "child_default",
                        "child_named",
                        "child_empty",
                        "custom_label",
                        "spawn_alias",
                        "spawn_alias_union",
                        "patch_imposter",
                        "stdin_imposter",
                        "handler_mutation",
                    }
                    else "original"
                ]
            )
            outcomes = await runtime._repository.load_turn_tool_outcomes(
                runtime.thread_id, events[-1].turn_id
            )
            outcome = next(result for result in outcomes if result.tool_name == tool_name)
            assert all(
                not hasattr(item, "execution_input_json")
                for request in requests
                for item in request.items
            )
            if effects:
                executed = json.loads(outcome.execution_input_json)
                assert executed["version"] == 1 and executed["input_kind"] == "json"
                assert executed["arguments"] == {"value": effects[0]}
            else:
                assert outcome.execution_input_json is None
            if action in {"untrusted", "internal"}:
                assert not (tmp_path / "hook-input.json").exists()
                assert not any(isinstance(event, HookCompleted) for event in events)
            else:
                payload = json.loads((tmp_path / "hook-input.json").read_text())
                assert payload["tool_input"] == {"value": "original"}
                assert payload["tool_name"] == tool_name
                if action.startswith("child_"):
                    assert payload["agent_id"] == str(runtime.thread_id)
                    assert payload["agent_type"] == (
                        "reviewer"
                        if action == "child_named"
                        else ""
                        if action == "child_empty"
                        else "default"
                    )
                else:
                    assert "agent_id" not in payload and "agent_type" not in payload
                if action == "transcript_error":
                    assert payload["transcript_path"] is None
                    assert (tmp_path / "hook-transcript.jsonl").read_text() == "unavailable"
                else:
                    assert Path(payload["transcript_path"]).is_file()
                    assert "use probe" in (tmp_path / "hook-transcript.jsonl").read_text()
                assert len([e for e in events if isinstance(e, HookCompleted)]) == (
                    2 if action == "competing" else 1
                )
                if action in {"warning", "warning_invalid_control", "invalid_warning"}:
                    completed = next(e for e in events if isinstance(e, HookCompleted))
                    assert completed.run.status == (
                        "completed" if action == "warning" else "failed"
                    )
                    warnings = [
                        entry.text for entry in completed.run.entries if entry.kind == "warning"
                    ]
                    assert warnings == (
                        [] if action == "invalid_warning" else [("hook notice " * 500)[:4000]]
                    )
                    assert "hook notice" not in repr(requests[-1].items)
            if not nested:
                calls = [
                    i.call
                    for i in await runtime._repository.load_items(runtime.thread_id)
                    if isinstance(i, ToolCallItem)
                ]
                assert calls[0].arguments == {"value": "original"}
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
