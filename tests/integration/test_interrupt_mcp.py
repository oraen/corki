"""Interrupt uses the actual HTTP MCP client without resampling or undoing cancellation."""

import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity
from corki.mcp.client import HttpMCPClient
from corki.protocol.events import HookCompleted, TurnCancelled
from corki.tools import ToolRegistry


@pytest.mark.parametrize("action", ["cancel", "close"])
@pytest.mark.parametrize("result_kind", ["warning", "invalid", "error", "timeout", "untrusted"])
def test_http_interrupt_hook_preserves_cancellation_and_closes_response(
    tmp_path, monkeypatch, action, result_kind
):
    async def scenario():
        handler = {
            "type": "mcp_tool",
            "server": "policy",
            "tool": "review",
            "timeout": 1,
            "input": {
                "event": "${hook_event_name}",
                "turn": "${turn_id}",
                "session": "${session_id}",
            },
        }
        fingerprint, _ = command_identity(handler, event_name="Interrupt")
        path = tmp_path / "config.toml"
        definition = (
            '[[hooks.Interrupt]]\nmatcher="ignored"\n[[hooks.Interrupt.hooks]]\n'
            'type="mcp_tool"\nserver="policy"\ntool="review"\ntimeout=1\n'
            '[hooks.Interrupt.hooks.input]\nevent="${hook_event_name}"\n'
            'turn="${turn_id}"\nsession="${session_id}"\n'
            f"[hooks.state.{json.dumps(f'{path}:interrupt:0:0')}]\n"
            f"trusted_hash={json.dumps(fingerprint if result_kind != 'untrusted' else 'wrong')}\n"
        )
        started, response_closed = asyncio.Event(), asyncio.Event()
        calls, clients, events, samples = [], [], [], []

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'{"jsonrpc":"2.0",'
                await asyncio.Event().wait()

            async def aclose(self):
                response_closed.set()

        def respond(request):
            assert request.url.host == "fixture.invalid"
            assert not any(name.lower().startswith("x-codex-") for name in request.headers)
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
                calls.append(message["params"])
                if result_kind == "timeout":
                    return httpx.Response(
                        200, headers={"content-type": "application/json"}, stream=Body()
                    )
                result = {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"continue": False}
                                if result_kind == "invalid"
                                else {"systemMessage": "MCP_INTERRUPT_WARNING"}
                            ),
                        }
                    ],
                    "isError": result_kind == "error",
                }
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": message["id"], "result": result}
            )

        def factory(settings):
            client = HttpMCPClient(settings, transport=httpx.MockTransport(respond))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)

        class Model:
            async def stream(self, request):
                samples.append(request)
                started.set()
                await asyncio.Event().wait()
                yield

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                plugins_enabled=False,
                mcp_servers=(MCPServerSettings("policy", "http", url="https://fixture.invalid"),),
                configuration=LocalConfigState((ConfigLayer(path, "user", contents=definition),)),
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "state.db",
            home_path=tmp_path,
        )

        async def consume():
            try:
                async for event in runtime.stream("INTERRUPT_MCP"):
                    if isinstance(event, TurnCancelled) and result_kind == "timeout":
                        assert response_closed.is_set()
                    events.append(event)
            except asyncio.CancelledError:
                pass

        consumer = None
        try:
            await runtime._mcp_manager.start()
            consumer = asyncio.create_task(consume())
            async with asyncio.timeout(6):
                await started.wait()
                if action == "close":
                    await runtime.aclose()
                else:
                    await runtime.cancel_active()
                await consumer
            assert len(samples) == 1
            assert isinstance(events[-1], TurnCancelled)
            assert len([e for e in events if isinstance(e, TurnCancelled)]) == 1
            hooks = [e for e in events if isinstance(e, HookCompleted)]
            assert len(calls) == len(hooks) == (0 if result_kind == "untrusted" else 1)
            if calls:
                assert calls[0]["arguments"] == {
                    "event": "Interrupt",
                    "turn": str(events[-1].turn_id),
                    "session": str(runtime.session_id),
                }
                metadata = calls[0]["_meta"]
                assert set(metadata) == {"threadId", "progressToken"}
                assert metadata["threadId"] == str(runtime.thread_id)
                assert type(metadata["progressToken"]) is int
                assert hooks[0].run.status == (
                    "completed" if result_kind == "warning" else "failed"
                )
                if result_kind == "warning":
                    assert any(e.text == "MCP_INTERRUPT_WARNING" for e in hooks[0].run.entries)
            await runtime.aclose()
            assert all(client.is_closed for client in clients)
            assert len(calls) == (0 if result_kind == "untrusted" else 1)
        finally:
            await runtime.cancel_active()
            if consumer is not None:
                await asyncio.gather(consumer, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())
