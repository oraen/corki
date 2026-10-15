import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki.config import MCPServerSettings
from corki.mcp.client import PROTOCOL_VERSION, HttpMCPClient
from corki.mcp.manager import MCPManager
from corki.tools import ToolRegistry


def settings():
    return MCPServerSettings("docs", "http", url="https://fixture.test", timeout_seconds=30)


def response(message, session):
    if message["method"] == "initialize":
        result = {
            "serverInfo": {"name": "fixture", "version": "1"},
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
        }
    elif message["method"] == "tools/list":
        result = {"tools": [{"name": "read", "inputSchema": {"type": "object"}}]}
    else:
        result = {"content": [{"type": "text", "text": "read"}]}
    return httpx.Response(
        200,
        headers={"mcp-session-id": session},
        json={
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": result,
        },
    )


def test_closed_http_transport_is_replaced_and_cleanup_remains_idempotent(monkeypatch):
    async def scenario():
        clients, deletes = [], []

        def factory(setting):
            session = str(len(clients))

            def handler(request):
                if request.method == "DELETE":
                    deletes.append(session)
                    return httpx.Response(204)
                message = json.loads(request.content)
                if message["method"] == "notifications/initialized":
                    return httpx.Response(202)
                return response(message, session)

            client = HttpMCPClient(setting, transport=httpx.MockTransport(handler))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        manager = MCPManager((settings(),), ToolRegistry())
        try:
            await manager.start()
            await clients[0]._client.aclose()
            manager.request_reconcile()
            await manager.call_tool("docs", "read", {})
            assert len(clients) == 2 and clients[0].is_closed and not clients[1].is_closed
        finally:
            await manager.aclose()
        await manager.aclose()
        assert deletes == ["1"] and all(c.is_closed for c in clients)

    asyncio.run(scenario())


def test_shared_http_transport_uses_each_admitted_timeout_and_cancels_expired_request(monkeypatch):
    async def scenario():
        clients, calls = [], []
        old_entered, old_release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def handler(request):
            if request.method == "DELETE":
                return httpx.Response(204)
            message = json.loads(request.content)
            if message["method"] == "notifications/initialized":
                return httpx.Response(202)
            if message["method"] == "tools/call":
                label = message["params"]["arguments"]["label"]
                deadlines = tuple(clients[0].active_time._budgets)
                calls.append(
                    (
                        label,
                        dict(request.extensions["timeout"]),
                        deadlines[-1].when() - asyncio.get_running_loop().time(),
                    )
                )
                if label == "old":
                    old_entered.set()
                    await old_release.wait()
                if label == "expire":
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancelled.set()
            return response(message, "same-session")

        def factory(setting):
            client = HttpMCPClient(setting, transport=httpx.MockTransport(handler))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        manager = MCPManager((settings(),), ToolRegistry())
        await manager.start()
        old = asyncio.create_task(manager.call_tool("docs", "read", {"label": "old"}))
        try:
            await asyncio.wait_for(old_entered.wait(), 2)
            manager.request_reconcile((replace(settings(), timeout_seconds=70),))
            await manager.call_tool("docs", "read", {"label": "new"})
            manager.request_reconcile((replace(settings(), timeout_seconds=0.01),))
            with pytest.raises(TimeoutError):
                await manager.call_tool("docs", "read", {"label": "expire"})
            assert cancelled.is_set()
            old_release.set()
            await old
            manager.request_reconcile((replace(settings(), timeout_seconds=80),))
            await manager.call_tool("docs", "read", {"label": "after"})
            expected = (("old", 30), ("new", 70), ("expire", 0.01), ("after", 80))
            assert [name for name, _, _ in calls] == [name for name, _ in expected]
            for (_, actual, remaining), (_, budget) in zip(calls, expected, strict=True):
                assert set(actual) == {"connect", "read", "write", "pool"}
                # The active-time owner, not HTTPX's unpausable wall clock, enforces
                # each admitted view's remaining operation budget.
                assert all(value is None for value in actual.values())
                assert budget * 0.9 <= remaining <= budget
            assert len(clients) == 1 and clients[0].settings.timeout_seconds == 30
        finally:
            old_release.set()
            await asyncio.gather(old, return_exceptions=True)
            await manager.aclose()
        assert clients[0].is_closed

    asyncio.run(scenario())
