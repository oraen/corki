"""Lifecycle policy around real ordinary-model compaction and history installation."""

import asyncio
import hashlib
import json
import shlex
import sys

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.events import HookCompleted, TurnCancelled, TurnCompleted
from corki.protocol.ids import ToolCallId
from corki.protocol.items import AssistantMessageItem, CompactionItem, ToolCallItem, new_step_id
from corki.protocol.tools import ToolCall, ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("trigger", ["manual", "auto"])
@pytest.mark.parametrize("event", ["PreCompact", "PostCompact"])
@pytest.mark.parametrize("policy", ["stop", "allow", "invalid", "untrusted"])
@pytest.mark.parametrize("backend", ["command", "http_mcp"])
def test_trusted_compact_hook_stops_at_its_install_boundary(
    tmp_path, monkeypatch, trigger, event, policy, backend
):
    async def scenario():
        source = tmp_path / "config.toml"
        marker = tmp_path / "hook-input.json"
        output = (
            {"decision": "block", "reason": "invalid for compact"}
            if policy == "invalid"
            else {"continue": policy != "stop", "stopReason": "COMPACT_POLICY_STOP"}
        )
        command = shlex.join(
            [
                sys.executable,
                "-c",
                "import json,sys; from pathlib import Path; p=json.load(sys.stdin); "
                f"Path({str(marker)!r}).write_text(json.dumps(p)); "
                f"print({json.dumps(output)!r})",
            ]
        )
        event_key = "pre_compact" if event == "PreCompact" else "post_compact"
        identity = {
            "event_name": event_key,
            "matcher": trigger,
            "hooks": [{"type": "command", "command": command, "timeout": 600, "async": False}],
        }
        calls = []
        servers = ()
        definition = f'type="command"\ncommand={json.dumps(command)}\n'
        if backend == "http_mcp":
            inputs = {"hook_event_name": "${hook_event_name}", "trigger": "${trigger}"}
            identity["hooks"] = [
                {
                    "type": "mcp_tool",
                    "server": "policy",
                    "tool": "review",
                    "input": inputs,
                    "timeout": 600,
                }
            ]
            definition = (
                'type="mcp_tool"\nserver="policy"\ntool="review"\n'
                f'[hooks.{event}.hooks.input]\nhook_event_name="${{hook_event_name}}"\n'
                'trigger="${trigger}"\n'
            )
            servers = (MCPServerSettings("policy", "http", url="https://fixture.invalid"),)

            def respond(request):
                assert request.url.host == "fixture.invalid"
                if request.method == "DELETE":
                    return httpx.Response(204)
                message = json.loads(request.content)
                if "id" not in message:
                    return httpx.Response(202)
                if message["method"] == "initialize":
                    result = {
                        "capabilities": {},
                        "protocolVersion": "2025-06-18",
                        "serverInfo": {"name": "fixture", "version": "1"},
                    }
                elif message["method"] == "tools/list":
                    result = {"tools": [{"name": "review", "inputSchema": {"type": "object"}}]}
                else:
                    assert message["method"] == "tools/call"
                    assert message["params"]["_meta"]["threadId"] == str(runtime.thread_id)
                    calls.append(message)
                    marker.write_text(json.dumps(message["params"]["arguments"]))
                    result = {"content": [{"type": "text", "text": json.dumps(output)}]}
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": message["id"], "result": result}
                )

            monkeypatch.setattr(
                "corki.mcp.manager.create_client",
                lambda settings: HttpMCPClient(settings, transport=httpx.MockTransport(respond)),
            )
        fingerprint = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
        if policy == "untrusted":
            fingerprint = "sha256:untrusted"
        document = (
            f"[[hooks.{event}]]\nmatcher={json.dumps(trigger)}\n"
            f"[[hooks.{event}.hooks]]\n{definition}"
            f"[hooks.state.{json.dumps(f'{source}:{event_key}:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        summaries, normal, effects = [], [], []

        class Large:
            spec = ToolSpec("large", "large observation", {"type": "object"})

            async def execute(self, call, context):
                effects.append(call.id)
                return ToolResult(call.id, call.name, "IMPORTANT FACT " + "x" * 16000)

        class Model:
            async def stream(self, request):
                turn, step = request.items[-1].turn_id, new_step_id()
                if getattr(request.items[-1], "content", None) == "SUMMARIZE_FOR_TEST":
                    summaries.append(request)
                    yield ModelCompleted((AssistantMessageItem("IMPORTANT FACT", turn, step),))
                else:
                    normal.append(request)
                    if len(normal) == 1:
                        yield ModelCompleted(
                            (
                                ToolCallItem(
                                    ToolCall(ToolCallId("large-result"), "large", {}),
                                    turn,
                                    step,
                                ),
                            )
                        )
                    else:
                        yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Large())
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                tool_mode="direct",
                context_window_tokens=8000,
                auto_compact_tokens=3000,
                compact_prompt="SUMMARIZE_FOR_TEST",
                mcp_servers=servers,
                configuration=LocalConfigState((ConfigLayer(source, "user", contents=document),)),
            ),
            registry=registry,
            model=Model(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )
        events = []
        try:
            if servers:
                await runtime._mcp_manager.start()
            try:
                stream = (
                    runtime.compact() if trigger == "manual" else runtime.stream("CURRENT INPUT")
                )
                async for item in stream:
                    events.append(item)
            except asyncio.CancelledError:
                pass
            assert marker.exists() == (policy != "untrusted")
            if servers:
                assert len(calls) == (0 if policy == "untrusted" else 1)
            if marker.exists():
                payload = json.loads(marker.read_text())
                assert payload["hook_event_name"] == event and payload["trigger"] == trigger
            stops_before = policy == "stop" and event == "PreCompact"
            assert len(summaries) == (0 if stops_before else 1)
            stored = await runtime._repository.load_items(runtime.thread_id)
            assert sum(isinstance(i, CompactionItem) for i in stored) == (not stops_before)
            completed = [e for e in events if isinstance(e, HookCompleted)]
            assert len(completed) == (0 if policy == "untrusted" else 1)
            if completed:
                assert (
                    completed[0].run.status
                    == {"stop": "stopped", "allow": "completed", "invalid": "failed"}[policy]
                )
            assert sum(isinstance(e, TurnCancelled) for e in events) == (policy == "stop")
            assert sum(isinstance(e, TurnCompleted) for e in events) == (policy != "stop")
            assert len(normal) == ((1 if policy == "stop" else 2) if trigger == "auto" else 0)
            assert len(effects) == (1 if trigger == "auto" else 0)
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("event", ["PreCompact", "PostCompact"])
@pytest.mark.parametrize("origin", ["root", "child", "default_child", "internal"])
def test_compact_hook_preserves_host_session_and_spawn_identity(
    tmp_path, monkeypatch, event, origin
):
    async def scenario():
        from corki.core.stop_hooks import command_identity
        from corki.protocol.ids import SessionId, ThreadId
        from corki.protocol.session_source import SessionSource, SubAgentSource, ThreadSpawnSource

        source = tmp_path / "config.toml"
        event_key = "pre_compact" if event == "PreCompact" else "post_compact"
        fingerprint, _ = command_identity(
            {"type": "command", "command": "review"}, event_name=event
        )
        document = (
            f'[[hooks.{event}]]\n[[hooks.{event}.hooks]]\ntype="command"\ncommand="review"\n'
            f"[hooks.state.{json.dumps(f'{source}:{event_key}:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        provenance = SessionSource()
        if origin in {"child", "default_child"}:
            provenance = SessionSource.subagent(
                SubAgentSource(
                    "thread_spawn",
                    ThreadSpawnSource(
                        ThreadId("parent"),
                        1,
                        agent_role="reviewer" if origin == "child" else None,
                    ),
                )
            )
        elif origin == "internal":
            provenance = SessionSource.internal("memory_consolidation")
        payloads = []

        async def runner(command, payload, **kwargs):
            payloads.append(payload)
            return {"exit_code": 0, "stdout": "{}", "stderr": ""}

        monkeypatch.setattr("corki.core.compact_hooks.run_command", runner)

        class Model:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("summary", request.items[-1].turn_id, new_step_id()),)
                )

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
            session_id=SessionId("explicit-host-session"),
            session_source=provenance,
        )
        try:
            events = [e async for e in runtime.compact()]
            assert isinstance(events[-1], TurnCompleted)
            assert len(payloads) == 1
            payload = payloads[0]
            assert runtime.thread_id != runtime.session_id
            assert payload["session_id"] == "explicit-host-session"
            if origin in {"child", "default_child"}:
                assert payload["agent_id"] == str(runtime.thread_id)
                assert payload["agent_type"] == ("reviewer" if origin == "child" else "default")
            else:
                assert "agent_id" not in payload and "agent_type" not in payload
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("event", ["PreCompact", "PostCompact"])
@pytest.mark.parametrize("commit_failure", [False, True])
def test_concurrent_compact_hooks_publish_in_configuration_order(
    tmp_path, monkeypatch, event, commit_failure
):
    async def scenario():
        from corki.core.stop_hooks import command_identity
        from corki.protocol.events import HookStarted, TurnFailed

        source = tmp_path / "config.toml"
        event_key = "pre_compact" if event == "PreCompact" else "post_compact"
        document = f"[[hooks.{event}]]\n"
        for index, name in enumerate(("first", "second")):
            fingerprint, _ = command_identity(
                {"type": "command", "command": name}, event_name=event
            )
            document += (
                f'[[hooks.{event}.hooks]]\ntype="command"\ncommand="{name}"\n'
                f"[hooks.state.{json.dumps(f'{source}:{event_key}:0:{index}')}]\n"
                f"trusted_hash={json.dumps(fingerprint)}\n"
            )
        second_committed = asyncio.Event()
        first_entered, first_cleaned = asyncio.Event(), asyncio.Event()
        execution_order = []

        async def controlled_run(command, *args, **kwargs):
            if command.command == "first":
                first_entered.set()
                try:
                    await asyncio.wait_for(second_committed.wait(), 3)
                finally:
                    first_cleaned.set()
            else:
                await asyncio.wait_for(first_entered.wait(), 3)
            execution_order.append(command.command)
            return {"exit_code": 0, "stdout": "{}", "stderr": ""}

        monkeypatch.setattr("corki.core.compact_hooks.run_command", controlled_run)

        class Model:
            async def stream(self, request):
                yield ModelCompleted(
                    (AssistantMessageItem("summary", request.items[-1].turn_id, new_step_id()),)
                )

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
        complete = runtime._repository.complete_hook_execution

        async def observed_complete(*args):
            await complete(*args)
            if args[3].get("command") == "second":
                if commit_failure:
                    raise OSError("injected completion write failure")
                second_committed.set()

        monkeypatch.setattr(runtime._repository, "complete_hook_execution", observed_complete)
        try:
            events = [item async for item in runtime.compact()]
            assert first_cleaned.is_set()
            if commit_failure:
                assert isinstance(events[-1], TurnFailed)
                assert "injected completion write failure" in events[-1].error
                assert execution_order == ["second"]
                assert not any(isinstance(e, HookCompleted) for e in events)
                return
            assert isinstance(events[-1], TurnCompleted)
            assert execution_order == ["second", "first"]
            started = [e.run.key for e in events if isinstance(e, HookStarted)]
            finished = [e.run.key for e in events if isinstance(e, HookCompleted)]
            assert len(started) == 2
            assert finished == started
            assert started[0].endswith(":0:0") and started[1].endswith(":0:1")
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
