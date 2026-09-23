"""A committed summary cannot acquire newly configured PostCompact side effects."""

import asyncio
import json
from dataclasses import replace

import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.graph import GraphRunContext
from corki.core.runtime import _initial_state
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.events import TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.ids import SessionId, ToolCallId, new_turn_id
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
    ("event", "change", "window"),
    [
        ("PostCompact", "unchanged", "install"),
        ("PostCompact", "added", "install"),
        ("PostCompact", "removed", "install"),
        ("PostCompact", "invalid_version", "install"),
        ("PostCompact", "invalid_session", "install"),
        ("PostCompact", "legacy_session", "install"),
        ("PostCompact", "duplicate_command", "install"),
        *[
            ("PostCompact", f"payload_{field}", "install")
            for field in ("model", "cwd", "trigger", "transcript_path", "agent_id")
        ],
        *[
            ("PostCompact", f"mismatch_{field}", "install")
            for field in ("model", "cwd", "trigger", "transcript_path", "agent", "extra")
        ],
        ("PostCompact", "unchanged", "unknown"),
        ("PostCompact", "removed", "unknown"),
        ("PostCompact", "unchanged", "completed"),
        ("PostCompact", "removed", "completed"),
        ("PreCompact", "unchanged", "unknown"),
        ("PreCompact", "removed", "unknown"),
        ("PreCompact", "unchanged", "completed"),
        ("PreCompact", "removed", "completed"),
        *[
            (event, change, "receipt")
            for event in ("PreCompact", "PostCompact")
            for change in ("unchanged", "removed", "stopped", "invalid_receipt")
        ],
    ],
)
@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("hook_kind", ["command", "mcp_tool"])
def test_post_compact_plan_is_bound_before_history_install(
    tmp_path, monkeypatch, change, automatic, window, event, hook_kind
):
    async def scenario():
        source = tmp_path / "config.toml"
        handler = (
            {
                "type": "mcp_tool",
                "server": "policy",
                "tool": "review",
                "input": {"session_id": "${session_id}"},
            }
            if hook_kind == "mcp_tool"
            else {"type": "command", "command": "review"}
        )
        fingerprint, _ = command_identity(handler, event_name=event)
        event_key = "pre_compact" if event == "PreCompact" else "post_compact"
        handler_document = (
            'type="mcp_tool"\nserver="policy"\ntool="review"\ninput={session_id="${session_id}"}\n'
            if hook_kind == "mcp_tool"
            else 'type="command"\ncommand="review"\n'
        )
        document = (
            f"[[hooks.{event}]]\n[[hooks.{event}.hooks]]\n{handler_document}"
            f"[hooks.state.{json.dumps(f'{source}:{event_key}:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        configured = LocalConfigState((ConfigLayer(source, "user", contents=document),))
        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            tool_mode="direct",
            context_window_tokens=8000,
            auto_compact_tokens=3000,
            compact_prompt="SUMMARY_REQUEST",
            mcp_servers=(
                (MCPServerSettings("policy", "http", url="https://fixture.invalid"),)
                if hook_kind == "mcp_tool"
                else ()
            ),
            configuration=LocalConfigState(()) if change == "added" else configured,
        )
        calls, summaries = [], []
        normal, effects = [], []

        class Large:
            spec = ToolSpec("large", "large observation", {"type": "object"})

            async def execute(self, call, context):
                effects.append(call.id)
                return ToolResult(call.id, call.name, "x" * 16000)

        async def runner(command, payload, **kwargs):
            calls.append(payload)
            return {
                "exit_code": 0,
                "stdout": '{"continue":false}' if change == "stopped" else "{}",
                "stderr": "",
            }

        monkeypatch.setattr("corki.core.compact_hooks.run_command", runner)

        class Client:
            is_closed = False
            server_instructions = None

            def __init__(self, settings):
                self.settings = settings

            async def start(self):
                pass

            async def list_tools(self):
                return ({"name": "review", "inputSchema": {"type": "object"}},)

            async def request(self, method, params):
                assert method == "tools/call"
                calls.append(params["arguments"])
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": '{"continue":false}' if change == "stopped" else "{}",
                        }
                    ]
                }

            async def aclose(self):
                self.is_closed = True

        if hook_kind == "mcp_tool":
            monkeypatch.setattr("corki.mcp.manager.create_client", Client)

        class Model:
            async def stream(self, request):
                if automatic and getattr(request.items[-1], "content", None) != "SUMMARY_REQUEST":
                    normal.append(request)
                    if len(normal) == 1:
                        yield ModelCompleted(
                            (
                                ToolCallItem(
                                    ToolCall(ToolCallId("large-call"), "large", {}),
                                    request.items[-1].turn_id,
                                    new_step_id(),
                                ),
                            )
                        )
                    else:
                        yield ModelCompleted(())
                    return
                summaries.append(request)
                yield ModelCompleted(
                    (AssistantMessageItem("summary", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        class Sink:
            async def emit(self, event):
                pass

        async def create(config, thread=None):
            registry = ToolRegistry()
            registry.register(Large())
            return await LangGraphRuntime.acreate(
                settings=config,
                model=Model(),
                registry=registry,
                database_path=tmp_path / "state.db",
                home_path=tmp_path,
                thread_id=thread,
                session_id=SessionId("saved-host-session") if thread is None else None,
            )

        warm = await create(settings)
        try:
            await warm._ensure_ready()
            if hook_kind == "mcp_tool":
                await warm._mcp_manager.start()
            thread, turn = warm.thread_id, new_turn_id()
            user = UserMessageItem("CURRENT INPUT", turn) if automatic else None
            await warm._repository.save_turn(
                TurnRecord(
                    turn,
                    thread,
                    TurnStatus.RUNNING,
                    "CURRENT INPUT" if automatic else "",
                    operation="normal" if automatic else "compact",
                )
            )
            if user is not None:
                await warm._repository.append_items(thread, (user,))
            append = warm._repository.append_items
            complete = warm._repository.complete_hook_execution
            save_batch = warm._repository.save_hook_batch

            async def after_install(thread_id, items):
                await append(thread_id, items)
                if any(isinstance(item, CompactionItem) for item in items):
                    raise OSError("injected Post boundary")

            async def completion_boundary(*args):
                if window == "completed":
                    await complete(*args)
                raise OSError("injected Post boundary")

            async def receipt_boundary(*args):
                await save_batch(*args)
                if f":{event}:" in args[2] and args[2].endswith(":receipt"):
                    raise OSError("injected Post boundary")

            with monkeypatch.context() as patch:
                if window == "install":
                    patch.setattr(warm._repository, "append_items", after_install)
                elif window == "receipt":
                    patch.setattr(warm._repository, "save_hook_batch", receipt_boundary)
                else:
                    patch.setattr(warm._repository, "complete_hook_execution", completion_boundary)
                with pytest.raises(OSError, match="injected Post boundary"):
                    await warm._compiled.ainvoke(
                        _initial_state(thread, turn, settings, user),
                        config=warm._graph_config(turn),
                        context=GraphRunContext(events=Sink()),
                    )
            installed = await warm._repository.load_items(thread)
            assert len(summaries) == (0 if event == "PreCompact" else 1)
            assert len(calls) == (0 if window == "install" else 1)
            assert any(isinstance(item, CompactionItem) for item in installed) == (
                event == "PostCompact"
            )
            facts = await warm._repository.load_hook_executions(thread, turn, "compact_hook:")
            if window != "install":
                assert len(facts) == 1
                assert (facts[0][2] is None) == (window == "unknown")
        finally:
            await warm.aclose()
        cold = await create(
            replace(
                settings, configuration=LocalConfigState(()) if change == "removed" else configured
            ),
            thread,
        )
        try:
            if hook_kind == "mcp_tool":
                await cold._mcp_manager.start()
            if change == "invalid_receipt":
                load_receipt = cold._repository.load_hook_batch

                async def corrupt_receipt(*args):
                    result = await load_receipt(*args)
                    if (
                        result is not None
                        and f":{event}:" in args[2]
                        and args[2].endswith(":receipt")
                    ):
                        receipt, records = result
                        return {**receipt, "stopped": "false"}, records
                    return result

                monkeypatch.setattr(cold._repository, "load_hook_batch", corrupt_receipt)
            if change in {
                "invalid_version",
                "invalid_session",
                "legacy_session",
                "duplicate_command",
            } or change.startswith(("payload_", "mismatch_")):
                load_batch = cold._repository.load_hook_batch

                async def corrupt_version(*args):
                    result = await load_batch(*args)
                    if result is not None and ":PostCompact:" in args[2]:
                        snapshot, records = result
                        if change == "invalid_version":
                            return {**snapshot, "version": True}, records
                        if change == "duplicate_command":
                            return {
                                **snapshot,
                                "commands": [*snapshot["commands"], *snapshot["commands"]],
                            }, records
                        if change.startswith("payload_"):
                            field = change.removeprefix("payload_")
                            return {
                                **snapshot,
                                "payload": {**snapshot["payload"], field: ["invalid"]},
                            }, records
                        if change.startswith("mismatch_"):
                            field = change.removeprefix("mismatch_")
                            if field == "agent":
                                return {
                                    **snapshot,
                                    "payload": {
                                        **snapshot["payload"],
                                        "agent_id": "foreign-agent",
                                        "agent_type": "foreign-role",
                                    },
                                }, records
                            if field == "extra":
                                return {
                                    **snapshot,
                                    "payload": {**snapshot["payload"], "scope": "foreign"},
                                }, records
                            value = {
                                "model": "foreign-model",
                                "cwd": "/foreign-cwd",
                                "trigger": "manual" if automatic else "auto",
                                "transcript_path": "/foreign/transcript.jsonl",
                            }[field]
                            return {
                                **snapshot,
                                "payload": {**snapshot["payload"], field: value},
                            }, records
                        return {
                            **snapshot,
                            "payload": {
                                **snapshot["payload"],
                                "session_id": str(thread)
                                if change == "legacy_session"
                                else "foreign-session",
                            },
                        }, records
                    return result

                monkeypatch.setattr(cold._repository, "load_hook_batch", corrupt_version)
            events = []
            if change == "stopped":
                with pytest.raises(asyncio.CancelledError):
                    async for emitted in cold.resume_pending():
                        events.append(emitted)
            else:
                events = [e async for e in cold.resume_pending()]
            before_summary = event == "PreCompact" and (
                window == "unknown" or change in {"stopped", "invalid_receipt"}
            )
            assert len(summaries) == (0 if before_summary else 1)
            assert len(calls) == (
                1 if window != "install" or change in {"unchanged", "legacy_session"} else 0
            )
            if calls:
                assert calls[0]["session_id"] == (
                    str(thread) if change == "legacy_session" else "saved-host-session"
                )
                assert cold.session_id == "saved-host-session"
            failed = (
                change == "invalid_receipt"
                or change.startswith("payload_")
                or change.startswith("mismatch_")
                or change == "duplicate_command"
                or window == "unknown"
                or (
                    window == "install"
                    and change in {"removed", "invalid_version", "invalid_session"}
                )
            )
            expected_terminal = (
                TurnCancelled if change == "stopped" else (TurnFailed if failed else TurnCompleted)
            )
            assert isinstance(events[-1], expected_terminal)
            if change == "invalid_receipt":
                assert "Invalid compaction hook completion receipt" in events[-1].error
            elif change == "invalid_version":
                assert "Invalid compaction hook snapshot" in events[-1].error
            elif change == "invalid_session":
                assert "Compaction hook session identity mismatch" in events[-1].error
            elif change.startswith("payload_"):
                assert "Invalid compaction hook payload" in events[-1].error
            elif change.startswith("mismatch_"):
                assert "Compaction hook payload identity mismatch" in events[-1].error
            elif change == "duplicate_command":
                assert "Duplicate compaction hook command" in events[-1].error
            elif failed:
                assert ("unknown" if window == "unknown" else "authorization changed") in events[
                    -1
                ].error
            if window != "install":
                assert (
                    await cold._repository.load_hook_executions(thread, turn, "compact_hook:")
                    == facts
                )
            stored = await cold._repository.load_items(thread)
            assert stored[: len(installed)] == installed
            assert sum(isinstance(item, CompactionItem) for item in stored) == (
                0 if before_summary else 1
            )
            assert len(effects) == (1 if automatic else 0)
            assert [e async for e in cold.resume_pending()] == []
        finally:
            await cold.aclose()

    asyncio.run(scenario())
