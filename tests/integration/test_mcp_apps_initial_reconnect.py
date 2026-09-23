"""Ordinary startup failure needs explicit refresh; required failures block admission."""

import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.models import ModelCompleted
from corki.tools import ToolRegistry


@pytest.mark.parametrize("name", ["codex_apps", "ordinary"])
def test_initial_failure_recovers_only_after_explicit_refresh(tmp_path, monkeypatch, name):
    async def scenario():
        clients = []
        retry = asyncio.Event()

        def factory(settings):
            attempt = len(clients)

            async def respond(request):
                assert str(request.url).rstrip("/") == "https://fixture.invalid"
                packet = json.loads(request.content)
                if packet["method"] == "notifications/initialized":
                    return httpx.Response(202)
                if packet["method"] == "initialize":
                    return httpx.Response(
                        200,
                        json={
                            "jsonrpc": "2.0",
                            "id": packet["id"],
                            "result": {
                                "protocolVersion": "2025-06-18",
                                "capabilities": {},
                                "serverInfo": {"name": name, "version": "1"},
                            },
                        },
                    )
                assert packet["method"] == "tools/list"
                if attempt:
                    retry.set()
                    return httpx.Response(
                        200, json={"jsonrpc": "2.0", "id": packet["id"], "result": {"tools": []}}
                    )
                return httpx.Response(
                    200,
                    json={
                        "jsonrpc": "2.0",
                        "id": packet["id"],
                        "error": {"code": -32000, "message": "initial startup failed"},
                    },
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(respond))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)

        class Model:
            calls = 0

            async def stream(self, request):
                self.calls += 1
                yield ModelCompleted(())

            async def aclose(self):
                pass

        model = Model()
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                execution_permissions=None,
                mcp_servers=(MCPServerSettings(name, "http", url="https://fixture.invalid"),),
            ),
            model=model,
            registry=ToolRegistry(),
            database_path=tmp_path / "startup.db",
            home_path=tmp_path / "home",
        )
        try:
            await runtime._ensure_ready()
            initial = runtime._mcp_manager._preparation.tasks[name]
            assert await initial is False
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(retry.wait(), 0.05)
            assert len(clients) == 1
            runtime.request_mcp_refresh()
            await runtime._mcp_manager.refresh_if_dirty()
            assert retry.is_set() and len(clients) == 2
            assert await runtime.mcp_tool_catalog() == ()
            assert model.calls == 0
            assert initial.result() is False
        finally:
            await runtime.aclose()
        assert all(client.is_closed for client in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("slow_name", ["a_slow", "z_slow"])
@pytest.mark.parametrize("reverse_declarations", [False, True])
@pytest.mark.parametrize("failed_name", ["codex_apps", "ordinary"])
def test_required_validation_preserves_each_observed_outcome(
    tmp_path, monkeypatch, slow_name, reverse_declarations, failed_name
):
    async def scenario():
        clients, requests = [], []
        initial_entered, fail_initial = asyncio.Event(), asyncio.Event()
        slow_entered, finish_slow = asyncio.Event(), asyncio.Event()

        def factory(settings):
            attempt = sum(client.settings.name == settings.name for client in clients)

            async def respond(request):
                assert str(request.url).rstrip("/") == "https://fixture.invalid"
                packet = json.loads(request.content)
                if packet["method"] == "notifications/initialized":
                    return httpx.Response(202)
                if packet["method"] == "initialize":
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": settings.name, "version": "1"},
                    }
                else:
                    assert packet["method"] == "tools/list"
                    if settings.name == slow_name:
                        slow_entered.set()
                        await finish_slow.wait()
                    elif attempt == 0:
                        initial_entered.set()
                        await fail_initial.wait()
                        return httpx.Response(
                            200,
                            json={
                                "jsonrpc": "2.0",
                                "id": packet["id"],
                                "error": {"code": -32000, "message": "initial startup failed"},
                            },
                        )
                    else:
                        raise AssertionError("startup failure must not silently retry")
                    result = {
                        "tools": [
                            {
                                "name": "ready",
                                "inputSchema": {"type": "object"},
                                "_meta": {"connector_id": "fixture"},
                            }
                        ]
                    }
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(respond))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        names = [failed_name, slow_name]
        if reverse_declarations:
            names.reverse()
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                execution_permissions=None,
                mcp_servers=tuple(
                    MCPServerSettings(name, "http", url="https://fixture.invalid", required=True)
                    for name in names
                ),
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "required.db",
            home_path=tmp_path / "home",
        )

        async def run():
            return [event async for event in runtime.stream("admit only after required checks")]

        turn = asyncio.create_task(run())
        try:
            await asyncio.wait_for(initial_entered.wait(), 2)
            await asyncio.wait_for(slow_entered.wait(), 2)
            # Required admission observes the initial failure regardless of ordering.
            await asyncio.sleep(0)
            generation = runtime._mcp_manager._preparation
            fail_initial.set()
            assert await asyncio.wait_for(generation.tasks[failed_name], 2) is False
            assert failed_name not in generation.staged
            assert not requests and not turn.done()
            finish_slow.set()
            with pytest.raises(RuntimeError, match=f"{failed_name}.*initial startup failed"):
                await asyncio.wait_for(turn, 2)
            assert not requests
        finally:
            fail_initial.set()
            finish_slow.set()
            if not turn.done():
                turn.cancel()
            await asyncio.gather(turn, return_exceptions=True)
            await runtime.aclose()
        assert len(clients) == 2 and all(client.is_closed for client in clients)

    asyncio.run(scenario())
