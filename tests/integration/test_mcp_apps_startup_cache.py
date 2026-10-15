"""Required versus optional startup controls sampling, never a platform name."""

import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.mcp.tool_catalog_cache import MCPToolCatalogCache
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.items import AssistantMessageItem, new_step_id
from corki.tools import ToolRegistry


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("name", ["codex_apps", "ordinary"])
@pytest.mark.parametrize("required,cancel", [(True, False), (True, True), (False, False)])
def test_uncached_required_startup_gates_model_sampling(
    tmp_path, monkeypatch, nested, name, required, cancel
):
    async def scenario():
        started, release, sampled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        clients = []

        def factory(settings):
            async def respond(request):
                packet = json.loads(request.content)
                if packet["method"] == "notifications/initialized":
                    return httpx.Response(202)
                if packet["method"] == "initialize":
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": name, "version": "1"},
                    }
                else:
                    assert packet["method"] == "tools/list"
                    started.set()
                    await release.wait()
                    result = {
                        "tools": [
                            {
                                "name": "needle",
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
                sampled.set()
                if required:
                    assert release.is_set(), "Required catalog omitted before startup completed"
                    if nested:
                        assert f"mcp__{name}__needle" in next(
                            t.description for t in request.tools if t.name == "exec"
                        )
                    else:
                        assert f"mcp__{name}::needle" in {t.name for t in request.tools}
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                skills_enabled=False,
                execution_permissions=None,
                tool_mode="code_mode_only" if nested else "direct",
                tool_search_mode="disabled",
                mcp_servers=(
                    MCPServerSettings(
                        name, "http", url="https://fixture.invalid", required=required
                    ),
                ),
                mcp_optional_startup_grace_ms=1,
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "startup.db",
            home_path=tmp_path / "home",
            mcp_tool_catalog_cache=MCPToolCatalogCache(),
        )

        async def consume():
            events = []
            try:
                async for event in runtime.stream("inspect tools"):
                    events.append(event)
            except asyncio.CancelledError:
                assert cancel
            return events

        running = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(started.wait(), 3)
            if required:
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(sampled.wait(), 0.1)
            else:
                await asyncio.wait_for(sampled.wait(), 3)
            if cancel:
                await runtime.cancel_active()
            else:
                release.set()
            events = await asyncio.wait_for(running, 3)
            if cancel:
                # Required initialization precedes Turn admission: cancellation
                # propagates without inventing a Turn or a terminal event.
                assert events == []
                assert not sampled.is_set()
                assert runtime._compiled is None and runtime._active_run is None
                assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
                assert not await runtime._repository.load_items(runtime.thread_id)
            else:
                assert isinstance(events[-1], TurnCompleted), events[-1]
        finally:
            release.set()
            if not running.done():
                running.cancel()
            await asyncio.gather(running, return_exceptions=True)
            await runtime.aclose()
        assert len(clients) == 1 and clients[0].is_closed

    asyncio.run(scenario())
