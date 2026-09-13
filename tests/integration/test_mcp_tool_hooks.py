"""MCP-backed hooks are host-configured policy, not model-selected tool calls."""

import asyncio
import hashlib
import json
from dataclasses import replace

import httpx
import pytest
from test_code_mode import request_call

from corki.config import CorkiSettings, MCPServerSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.events import HookCompleted, TurnCancelled, TurnCompleted, TurnFailed
from corki.protocol.ids import ThreadId
from corki.protocol.items import AssistantMessageItem, ContextItem, ToolResultItem, new_step_id
from corki.protocol.session_source import (
    SessionSource,
    SessionSourceKind,
    SubAgentSource,
    ThreadSpawnSource,
)
from corki.protocol.tools import ToolResult, ToolSpec
from corki.tools import ToolRegistry


@pytest.mark.parametrize("event", ["PreToolUse", "PostToolUse"])
@pytest.mark.parametrize("failure", ["timeout", "cancel"])
def test_http_mcp_hook_failure_closes_response_without_replaying(
    tmp_path, monkeypatch, event, failure
):
    async def scenario():
        from corki.core.stop_hooks import command_identity

        source = tmp_path / "config.toml"
        handler = {"type": "mcp_tool", "server": "policy", "tool": "review", "timeout": 1}
        key = "pre_tool_use" if event == "PreToolUse" else "post_tool_use"
        fingerprint, _ = command_identity(handler, event_name=event, matcher="probe")
        definition = (
            f'[[hooks.{event}]]\nmatcher="probe"\n'
            f'[[hooks.{event}.hooks]]\ntype="mcp_tool"\n'
            'server="policy"\ntool="review"\ntimeout=1\n'
            f"[hooks.state.{json.dumps(f'{source}:{key}:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        started, cleaned = asyncio.Event(), asyncio.Event()
        calls, effects, events, clients = [], [], [], []

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                started.set()
                yield b'{"jsonrpc":"2.0",'
                await asyncio.Event().wait()

            async def aclose(self):
                cleaned.set()

        def handle(request):
            assert request.url.host == "fixture.invalid"
            if request.method == "DELETE":
                return httpx.Response(204)
            message = json.loads(request.content)
            if "id" not in message:
                return httpx.Response(202)
            if message["method"] == "initialize":
                result = {
                    "capabilities": {},
                    "serverInfo": {"name": "fixture", "version": "1"},
                    "protocolVersion": "2025-06-18",
                }
            elif message["method"] == "tools/list":
                result = {"tools": [{"name": "review", "inputSchema": {"type": "object"}}]}
            else:
                assert message["method"] == "tools/call"
                assert message["params"]["_meta"]["threadId"] == str(runtime._thread_id)
                calls.append(message)
                return httpx.Response(
                    200, headers={"content-type": "application/json"}, stream=Body()
                )
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": message["id"], "result": result}
            )

        def factory(settings):
            client = HttpMCPClient(settings, transport=httpx.MockTransport(handle))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)

        class Probe:
            spec = ToolSpec("probe", "side effect", {"type": "object"})

            async def execute(self, call, context):
                effects.append(call.id)
                return ToolResult(call.id, call.name, "executed")

        class Model:
            count = 0

            async def stream(self, request):
                self.count += 1
                if self.count == 1:
                    yield request_call(request, "probe", {})
                else:
                    assert cleaned.is_set()
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
                tool_mode="direct",
                mcp_servers=(MCPServerSettings("policy", "http", url="https://fixture.invalid"),),
                configuration=LocalConfigState((ConfigLayer(source, "user", contents=definition),)),
            ),
            registry=registry,
            model=Model(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )

        async def consume():
            async for item in runtime.stream("run probe"):
                if isinstance(item, (TurnCompleted, TurnCancelled, TurnFailed)):
                    assert cleaned.is_set(), "terminal preceded HTTP response cleanup"
                events.append(item)

        consumer = None
        try:
            await runtime._mcp_manager.start()
            consumer = asyncio.create_task(consume())
            await asyncio.wait_for(started.wait(), 3)
            if failure == "cancel":
                await runtime.cancel_active()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(consumer, 5)
            else:
                await asyncio.wait_for(consumer, 5)
            assert len(calls) == 1
            assert cleaned.is_set()
            assert len(effects) == (0 if failure == "cancel" and event == "PreToolUse" else 1)
            terminal = [
                e for e in events if isinstance(e, (TurnCompleted, TurnCancelled, TurnFailed))
            ]
            assert len(terminal) == 1
            assert isinstance(terminal[0], TurnCancelled if failure == "cancel" else TurnCompleted)
            if failure == "timeout":
                hooks = [e for e in events if isinstance(e, HookCompleted)]
                assert len(hooks) == 1 and hooks[0].run.status == "failed"
        finally:
            await runtime.cancel_active()
            if consumer is not None:
                await asyncio.gather(consumer, return_exceptions=True)
            await runtime.aclose()
        assert all(client.is_closed for client in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("event", ["PreToolUse", "PostToolUse"])
@pytest.mark.parametrize(
    "policy",
    [
        "trusted",
        "untrusted",
        "revoked",
        "modified",
        "tool_disabled",
        "server_removed",
        "server_pending",
    ],
)
def test_ready_trusted_mcp_hook_controls_tool_boundary(
    tmp_path, monkeypatch, nested, event, policy
):
    async def scenario():
        source = tmp_path / "config.toml"
        handler = {
            "type": "mcp_tool",
            "server": "policy",
            "tool": "review",
            "input": {"value": "${tool_input.value}"},
            "timeout": 10,
        }
        # Native NormalizedHookIdentity -> TOML value -> canonical JSON hash.
        event_key = "pre_tool_use" if event == "PreToolUse" else "post_tool_use"
        identity = {"event_name": event_key, "matcher": "probe", "hooks": [handler]}
        fingerprint = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
        definition = (
            f'[[hooks.{event}]]\nmatcher="probe"\n'
            f'[[hooks.{event}.hooks]]\ntype="mcp_tool"\n'
            'server="policy"\ntool="review"\ntimeout=10\n'
            f'[hooks.{event}.hooks.input]\nvalue="${{tool_input.value}}"\n'
            f"[hooks.state.{json.dumps(f'{source}:{event_key}:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        if policy == "untrusted":
            definition = definition.replace(fingerprint, "sha256:untrusted")
        elif policy == "revoked":
            definition += "enabled=false\n"
        elif policy == "modified":
            definition = definition.replace('tool="review"', 'tool="changed"')
        reviews, effects, requests = [], [], []
        pending_started, pending_closed = asyncio.Event(), asyncio.Event()

        class Client:
            def __init__(self, settings):
                self.settings = settings
                self.is_closed = False
                self.server_instructions = None

            async def start(self):
                if self.settings.url.endswith("/pending"):
                    pending_started.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        pending_closed.set()

            async def list_tools(self):
                return ({"name": "review", "inputSchema": {"type": "object"}},)

            async def call_tool(self, name, arguments, **kwargs):
                reviews.append((name, arguments))
                return {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"decision": "block", "reason": "host policy denied"}
                            ),
                        }
                    ]
                }

            async def aclose(self):
                self.is_closed = True

            async def request(self, method, params):
                assert method == "tools/call"
                assert params["_meta"]["threadId"]
                return await self.call_tool(params["name"], params["arguments"])

        monkeypatch.setattr("corki.mcp.manager.create_client", Client)

        class Probe:
            spec = ToolSpec("probe", "side effect", {"type": "object"})

            async def execute(self, call, context):
                effects.append(call.id)
                return ToolResult(call.id, call.name, "executed")

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    if policy == "server_pending":
                        runtime._mcp_manager.request_refresh(
                            (replace(server, url="https://fixture.test/pending"),)
                        )
                    if policy in {"tool_disabled", "server_removed"}:
                        runtime._mcp_manager.request_reconcile(
                            (replace(server, disabled_tools=("review",)),)
                            if policy == "tool_disabled"
                            else ()
                        )
                    yield request_call(
                        request,
                        "exec" if nested else "probe",
                        'text(await tools.probe({value:"original"}));'
                        if nested
                        else {"value": "original"},
                    )
                else:
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        registry = ToolRegistry()
        registry.register(Probe())
        server = MCPServerSettings("policy", "http", url="https://fixture.test")
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                tool_mode="code_mode_only" if nested else "direct",
                mcp_servers=(server,),
                configuration=LocalConfigState((ConfigLayer(source, "user", contents=definition),)),
            ),
            registry=registry,
            model=Model(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )
        try:
            await runtime._mcp_manager.start()
            assert "policy" in runtime._mcp_manager._clients_by_name

            async def collect():
                return [event async for event in runtime.stream("run probe")]

            events = await asyncio.wait_for(collect(), 5)
            assert isinstance(events[-1], TurnCompleted)
            assert reviews == ([("review", {"value": "original"})] if policy == "trusted" else [])
            assert len(effects) == (0 if event == "PreToolUse" and policy == "trusted" else 1)
            results = [item for item in requests[-1].items if isinstance(item, ToolResultItem)]
            assert any(
                item.is_error and "host policy denied" in item.content for item in results
            ) == (policy == "trusted")
            completed = [item for item in events if isinstance(item, HookCompleted)]
            assert len(completed) == (0 if policy in {"untrusted", "revoked", "modified"} else 1)
        finally:
            await runtime.aclose()
        if policy in {"tool_disabled", "server_removed", "server_pending"}:
            assert completed[0].run.status == "failed"
        if policy == "server_pending":
            assert pending_started.is_set() and pending_closed.is_set()

    asyncio.run(scenario())


@pytest.mark.parametrize("source_kind", ["root", "child", "internal"])
def test_mcp_stop_hook_continues_once_and_respects_worker_identity(
    tmp_path, monkeypatch, source_kind
):
    async def scenario():
        from corki.core.stop_hooks import command_identity

        event = "SubagentStop" if source_kind == "child" else "Stop"
        event_key = "subagent_stop" if source_kind == "child" else "stop"
        handler = {
            "type": "mcp_tool",
            "server": "policy",
            "tool": "review",
            "input": {"active": "${stop_hook_active}", "event": "${hook_event_name}"},
        }
        fingerprint, _ = command_identity(handler, event_name=event, matcher="reviewer")
        path = tmp_path / "config.toml"
        definition = (
            f'[[hooks.{event}]]\nmatcher="reviewer"\n[[hooks.{event}.hooks]]\n'
            'type="mcp_tool"\nserver="policy"\ntool="review"\n'
            f'[hooks.{event}.hooks.input]\nactive="${{stop_hook_active}}"\nevent="${{hook_event_name}}"\n'
            f"[hooks.state.{json.dumps(f'{path}:{event_key}:0:0')}]\ntrusted_hash={json.dumps(fingerprint)}\n"
        )
        calls, requests = [], []

        class Client:
            def __init__(self, settings):
                self.settings, self.is_closed, self.server_instructions = settings, False, None

            async def start(self):
                pass

            async def list_tools(self):
                return ({"name": "review", "inputSchema": {"type": "object"}},)

            async def request(self, method, params):
                assert method == "tools/call"
                calls.append(params["arguments"])
                result = (
                    {}
                    if params["arguments"]["active"]
                    else {
                        "decision": "block",
                        "reason": "VERIFY_BEFORE_FINISH",
                    }
                )
                return {"content": [{"type": "text", "text": json.dumps(result)}]}

            async def aclose(self):
                self.is_closed = True

        monkeypatch.setattr("corki.mcp.manager.create_client", Client)

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 2:
                    assert any(
                        isinstance(i, ContextItem) and i.content == "VERIFY_BEFORE_FINISH"
                        for i in request.items
                    )
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        provenance = SessionSource()
        if source_kind == "child":
            provenance = SessionSource.subagent(
                SubAgentSource(
                    "thread_spawn", ThreadSpawnSource(ThreadId("parent"), 1, agent_role="reviewer")
                )
            )
        elif source_kind == "internal":
            provenance = SessionSource(SessionSourceKind.INTERNAL, "memory_consolidation")
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                mcp_servers=(MCPServerSettings("policy", "http", url="https://fixture.test"),),
                configuration=LocalConfigState((ConfigLayer(path, "user", contents=definition),)),
            ),
            model=Model(),
            registry=ToolRegistry(),
            session_source=provenance,
            database_path=tmp_path / "stop.db",
            home_path=tmp_path,
        )
        try:
            await runtime._mcp_manager.start()
            events = [item async for item in runtime.stream("finish")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(requests) == (1 if source_kind == "internal" else 2)
            assert calls == (
                []
                if source_kind == "internal"
                else [
                    {"active": False, "event": event},
                    {"active": True, "event": event},
                ]
            )
            completed = [item.run for item in events if isinstance(item, HookCompleted)]
            assert [item.status for item in completed] == (
                [] if source_kind == "internal" else ["blocked", "completed"]
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
