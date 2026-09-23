"""Interrupt effects belong to an interrupted root Turn, not every cancellation."""

import asyncio
import json
import shlex
import sys
from hashlib import sha256

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.protocol.events import HookCompleted, HookStarted, TurnCancelled
from corki.protocol.ids import ThreadId
from corki.protocol.session_source import SessionSource, SubAgentSource, ThreadSpawnSource
from corki.tools import ToolRegistry


def test_http_mcp_interrupt_timeout_closes_response_before_cancel_terminal(tmp_path, monkeypatch):
    async def scenario():
        from corki.core.stop_hooks import command_identity

        source = tmp_path / "config.toml"
        handler = {"type": "mcp_tool", "server": "policy", "tool": "record", "timeout": 1}
        fingerprint, _ = command_identity(handler, event_name="Interrupt")
        definition = (
            "[[hooks.Interrupt]]\n[[hooks.Interrupt.hooks]]\n"
            'type="mcp_tool"\nserver="policy"\ntool="record"\ntimeout=1\n'
            f"[hooks.state.{json.dumps(f'{source}:interrupt:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        entered, cleaned = asyncio.Event(), asyncio.Event()
        calls, clients, events = [], [], []

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                entered.set()
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
                result = {"tools": [{"name": "record", "inputSchema": {"type": "object"}}]}
            else:
                assert message["method"] == "tools/call"
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
        started = asyncio.Event()

        class Model:
            async def stream(self, request):
                started.set()
                await asyncio.Event().wait()
                yield

            async def aclose(self):
                pass

        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            mcp_servers=(MCPServerSettings("policy", "http", url="https://fixture.invalid"),),
            configuration=LocalConfigState((ConfigLayer(source, "user", contents=definition),)),
        )
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )

        async def consume():
            async for event in runtime.stream("INTERRUPTED_PROMPT"):
                if isinstance(event, TurnCancelled):
                    assert cleaned.is_set(), "terminal preceded HTTP response cleanup"
                events.append(event)

        consumer = None
        try:
            await runtime._mcp_manager.start()
            consumer = asyncio.create_task(consume())
            await asyncio.wait_for(started.wait(), 3)
            async with asyncio.timeout(8):
                await runtime.cancel_active(reason="interrupted")
                with pytest.raises(asyncio.CancelledError):
                    await consumer
            assert entered.is_set() and cleaned.is_set()
            assert len(calls) == 1
            assert isinstance(events[-1], TurnCancelled)
            hooks = [event for event in events if isinstance(event, HookCompleted)]
            assert len(hooks) == 1 and hooks[0].run.status == "failed"
            assert len([event for event in events if isinstance(event, TurnCancelled)]) == 1
        finally:
            await runtime.aclose()
            if consumer is not None:
                await asyncio.gather(consumer, return_exceptions=True)
        assert all(client.is_closed for client in clients)

        cold = await LangGraphRuntime.acreate(
            settings=settings,
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
            thread_id=runtime.thread_id,
        )
        try:
            assert [event async for event in cold.resume_pending()] == []
            assert len(calls) == 1
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", ["interrupted", "replaced"])
def test_mcp_interrupt_hook_runs_only_for_explicit_root_interrupt(tmp_path, monkeypatch, reason):
    async def scenario():
        from corki.core.stop_hooks import command_identity

        path = tmp_path / "config.toml"
        handler = {
            "type": "mcp_tool",
            "server": "policy",
            "tool": "record",
            "timeout": 1,
            "input": {"hook_event_name": "${hook_event_name}", "turn_id": "${turn_id}"},
        }
        fingerprint, _ = command_identity(handler, event_name="Interrupt")
        definition = (
            "[[hooks.Interrupt]]\n[[hooks.Interrupt.hooks]]\n"
            'type="mcp_tool"\nserver="policy"\ntool="record"\ntimeout=1\n'
            "[hooks.Interrupt.hooks.input]\n"
            'hook_event_name="${hook_event_name}"\nturn_id="${turn_id}"\n'
            f"[hooks.state.{json.dumps(f'{path}:interrupt:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        calls = []
        started = asyncio.Event()

        class Client:
            def __init__(self, settings):
                self.settings = settings
                self.server_instructions = None
                self.is_closed = False

            async def start(self):
                pass

            async def list_tools(self):
                return ({"name": "record", "inputSchema": {"type": "object"}},)

            async def request(self, method, params):
                assert method == "tools/call"
                calls.append((params["name"], params["arguments"], params["_meta"]))
                return {
                    "content": [
                        {"type": "text", "text": json.dumps({"systemMessage": "MCP_INTERRUPT"})}
                    ]
                }

            async def aclose(self):
                self.is_closed = True

        clients = []

        def create_client(settings):
            client = Client(settings)
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", create_client)

        class Model:
            async def stream(self, request):
                started.set()
                await asyncio.Event().wait()
                yield

            async def aclose(self):
                pass

        settings = CorkiSettings(
            tmp_path,
            skills_enabled=False,
            plugins_enabled=False,
            mcp_servers=(MCPServerSettings("policy", "http", url="https://fixture.test"),),
            configuration=LocalConfigState((ConfigLayer(path, "user", contents=definition),)),
        )
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )
        events = []

        async def consume():
            async for event in runtime.stream("INTERRUPTED_PROMPT"):
                events.append(event)

        consumer = None
        try:
            await runtime._mcp_manager.start()
            consumer = asyncio.create_task(consume())
            async with asyncio.timeout(8):
                await started.wait()
                await runtime.cancel_active(reason=reason)
                with pytest.raises(asyncio.CancelledError):
                    await consumer
            assert isinstance(events[-1], TurnCancelled)
            hooks = [event for event in events if isinstance(event, (HookStarted, HookCompleted))]
            if reason == "interrupted":
                assert len(calls) == 1
                assert calls[0][0] == "record"
                assert calls[0][1]["hook_event_name"] == "Interrupt"
                assert calls[0][1]["turn_id"] == str(events[-1].turn_id)
                assert len(hooks) == 2
                assert isinstance(hooks[0], HookStarted)
                assert isinstance(hooks[1], HookCompleted)
                assert hooks[1].run.status == "completed"
                assert any(entry.text == "MCP_INTERRUPT" for entry in hooks[1].run.entries)
            else:
                assert calls == []
                assert hooks == []
            await runtime.cancel_active()
            assert len(calls) == (1 if reason == "interrupted" else 0)
        finally:
            await runtime.aclose()
            if consumer is not None:
                await asyncio.gather(consumer, return_exceptions=True)
        assert all(client.is_closed for client in clients)
        cold = await LangGraphRuntime.acreate(
            settings=settings,
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
            thread_id=runtime.thread_id,
        )
        try:
            assert [event async for event in cold.resume_pending()] == []
            assert len(calls) == (1 if reason == "interrupted" else 0)
        finally:
            await cold.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("origin", ["root", "child", "review"])
@pytest.mark.parametrize("reason", ["interrupted", "replaced"])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_interrupt_hook_runs_before_terminal_only_for_interrupted_root(
    tmp_path, origin, reason, asynchronous
):
    async def scenario():
        gate = (
            "import time\nwhile not Path('release').exists(): time.sleep(0.01)\n"
            if asynchronous
            else ""
        )
        command = shlex.join(
            [
                sys.executable,
                "-c",
                "import json,sys; from pathlib import Path; p=json.load(sys.stdin)\n"
                + gate
                + "Path('interrupt.jsonl').open('a').write(json.dumps({'payload':p,"
                "'transcript':Path(p['transcript_path']).read_text()})+'\\n'); "
                "print(json.dumps({'systemMessage':'INTERRUPT_DIAGNOSTIC'}))",
            ]
        )
        # The async subprocess is deliberately held until after the terminal;
        # give that test the maximum supported Interrupt hook deadline so
        # worker scheduling does not consume its entire one-second budget.
        timeout = 3 if asynchronous else 1
        identity = {
            "event_name": "interrupt",
            "hooks": [
                {"type": "command", "command": command, "timeout": timeout, "async": asynchronous}
            ],
        }
        fingerprint = (
            "sha256:"
            + sha256(
                json.dumps(
                    identity, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                ).encode()
            ).hexdigest()
        )
        path = tmp_path / "config.toml"
        document = (
            "[[hooks.Interrupt]]\n[[hooks.Interrupt.hooks]]\n"
            f'type="command"\ncommand={json.dumps(command)}\ntimeout={timeout}\n'
            f"async={str(asynchronous).lower()}\n"
            f"[hooks.state.{json.dumps(f'{path}:interrupt:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint)}\n"
        )
        started = asyncio.Event()
        model_finished = asyncio.Event()

        class Model:
            async def stream(self, request):
                started.set()
                try:
                    await asyncio.Event().wait()
                    yield
                finally:
                    model_finished.set()

            async def aclose(self):
                pass

        source = SessionSource()
        if origin == "child":
            source = SessionSource.subagent(
                SubAgentSource("thread_spawn", ThreadSpawnSource(ThreadId("parent"), 1))
            )
        elif origin == "review":
            source = SessionSource.subagent(SubAgentSource("review"))
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                configuration=LocalConfigState((ConfigLayer(path, "user", contents=document),)),
            ),
            model=Model(),
            registry=ToolRegistry(),
            session_source=source,
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )
        events = []

        async def consume():
            async for event in runtime.stream("INTERRUPTED_PROMPT"):
                events.append(event)

        consumer = asyncio.create_task(consume())
        try:
            async with asyncio.timeout(8):
                await started.wait()
                await runtime.cancel_active(reason=reason)
                with pytest.raises(asyncio.CancelledError):
                    await consumer
            assert model_finished.is_set()
            assert isinstance(events[-1], TurnCancelled)
            assert len([event for event in events if isinstance(event, TurnCancelled)]) == 1
            expected = origin == "root" and reason == "interrupted"
            log = tmp_path / "interrupt.jsonl"
            if expected and asynchronous:
                assert not log.exists(), (
                    "Async interrupt must not hold the terminal until completion"
                )
                (tmp_path / "release").write_text("continue")
                async with asyncio.timeout(5):
                    while True:
                        _, records = await runtime._repository.load_hook_batch(
                            runtime.thread_id,
                            events[-1].turn_id,
                            f"interrupt_hook:{events[-1].turn_id}:",
                        )
                        if records and all(
                            record["result"] is not None for record in records.values()
                        ):
                            break
                        await asyncio.sleep(0.01)
            assert log.exists() == expected, "Interrupt dispatch does not match cancellation origin"
            hooks = [event for event in events if isinstance(event, (HookStarted, HookCompleted))]
            if expected:
                if asynchronous:
                    assert hooks == []
                else:
                    assert len(hooks) == 2
                    assert isinstance(hooks[0], HookStarted)
                    assert isinstance(hooks[1], HookCompleted)
                    assert hooks[1].run.status == "completed"
                    assert hooks[0].turn_id == hooks[1].turn_id == events[-1].turn_id
                records = [json.loads(line) for line in log.read_text().splitlines()]
                assert len(records) == 1
                payload = records[0]["payload"]
                assert set(payload) == {
                    "session_id",
                    "turn_id",
                    "transcript_path",
                    "cwd",
                    "hook_event_name",
                    "model",
                    "permission_mode",
                }
                assert payload["session_id"] == str(runtime.session_id)
                assert payload["turn_id"] == str(events[-1].turn_id)
                assert payload["hook_event_name"] == "Interrupt"
                assert "INTERRUPTED_PROMPT" in records[0]["transcript"]
                if not asynchronous:
                    assert any(
                        entry.text == "INTERRUPT_DIAGNOSTIC" for entry in hooks[1].run.entries
                    )
            else:
                assert hooks == []
            await runtime.cancel_active()
            await runtime.aclose()
            if expected:
                assert len(log.read_text().splitlines()) == 1
        finally:
            await runtime.aclose()
            await asyncio.gather(consumer, return_exceptions=True)

    asyncio.run(scenario())
