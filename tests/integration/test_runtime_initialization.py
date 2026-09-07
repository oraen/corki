"""Startup is owned until rollback completes, before any logical Turn is admitted."""

import asyncio
import json
import threading
from contextlib import suppress

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.core import runtime as runtime_module
from corki.mcp.client import PROTOCOL_VERSION, HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.items import UserMessageItem
from corki.tools import ToolRegistry


@pytest.mark.parametrize("boundary", ["thread", "mcp", "checkpoint"])
@pytest.mark.parametrize("action", ["cancel", "repeat_cancel", "stop", "close"])
def test_initialization_cancellation_joins_rollback_before_retry_or_close(
    tmp_path, monkeypatch, boundary, action
):
    async def scenario():
        reached, rolling_back, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        thread_reached, thread_release = threading.Event(), threading.Event()
        requests, events, clients, disposed = [], [], [], []
        block_once = True

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                disposed.append("model")

        def client_factory(settings):
            async def handle(request):
                nonlocal block_once
                if request.method == "DELETE":
                    if settings.name == "held" and not release.is_set():
                        rolling_back.set()
                        await release.wait()
                    disposed.append(settings.name)
                    return httpx.Response(204)
                message = json.loads(request.content)
                method = message["method"]
                if method == "notifications/initialized":
                    return httpx.Response(202)
                if method == "initialize":
                    result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}}}
                elif method == "tools/list":
                    if settings.name == "held" and block_once:
                        block_once = False
                        reached.set()
                        await asyncio.Event().wait()
                    result = {"tools": [{"name": "lookup", "inputSchema": {"type": "object"}}]}
                else:
                    raise AssertionError(method)
                return httpx.Response(
                    200,
                    headers={"mcp-session-id": settings.name},
                    json={"jsonrpc": "2.0", "id": message["id"], "result": result},
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(handle))
            clients.append(client)
            return client

        servers = (
            tuple(
                MCPServerSettings(name, "http", url=f"https://{name}.test/mcp")
                for name in ("ready", "held")
            )
            if boundary == "mcp"
            else ()
        )
        monkeypatch.setattr("corki.mcp.manager.create_client", client_factory)
        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                working_directory=tmp_path, skills_enabled=False, mcp_servers=servers
            ),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=Model(),
        )

        if boundary == "thread":
            original_write = runtime._repository._create_thread

            def held_write(*args):
                nonlocal block_once
                if block_once:
                    block_once = False
                    thread_reached.set()
                    assert thread_release.wait(5)
                original_write(*args)
                disposed.append("thread-write")

            monkeypatch.setattr(runtime._repository, "_create_thread", held_write)
        elif boundary == "checkpoint":
            factory = runtime_module.AsyncSqliteSaver.from_conn_string

            class HeldContext:
                def __init__(self, path):
                    self.context = factory(path)

                async def __aenter__(self):
                    saver = await self.context.__aenter__()
                    original_setup = saver.setup

                    async def setup():
                        nonlocal block_once
                        await original_setup()
                        if block_once:
                            block_once = False
                            reached.set()
                            await asyncio.Event().wait()

                    saver.setup = setup
                    return saver

                async def __aexit__(self, *error):
                    rolling_back.set()
                    await release.wait()
                    await self.context.__aexit__(*error)
                    disposed.append("checkpoint")

            monkeypatch.setattr(
                runtime_module.AsyncSqliteSaver, "from_conn_string", staticmethod(HeldContext)
            )

        async def consume():
            async for event in runtime.stream("not admitted", realtime=True):
                events.append(event)

        consumer = asyncio.create_task(consume())
        closing = None
        try:
            if boundary == "thread":
                assert await asyncio.to_thread(thread_reached.wait, 3)
            else:
                await asyncio.wait_for(reached.wait(), 3)
            if action == "close":
                closing = asyncio.create_task(runtime.aclose())
            elif action == "stop":
                await runtime.cancel_active()
                await runtime.cancel_active()
            else:
                consumer.cancel()
            if boundary != "thread":
                await asyncio.wait_for(rolling_back.wait(), 1)
            if action == "repeat_cancel":
                consumer.cancel()
            for _ in range(8):
                await asyncio.sleep(0)
            assert not consumer.done(), "startup must join writes/rollback despite cancellation"
            assert closing is None or not closing.done()
            assert not requests and not events and runtime._compiled is None
            release.set()
            thread_release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(consumer, 3)
            if closing is not None:
                await asyncio.wait_for(closing, 3)
            assert not runtime._realtime.active and runtime._active_run is None
            assert runtime._compiled is None and runtime._checkpointer is None
            assert await runtime._repository.latest_running_turn(runtime.thread_id) is None
            if boundary == "mcp":
                assert all(client._client.is_closed for client in clients)
                assert {"ready", "held"} <= set(disposed)
                assert not runtime._mcp_manager.tool_names
                assert not runtime._registry.specs()
            elif boundary == "checkpoint":
                assert "checkpoint" in disposed
            else:
                assert "thread-write" in disposed
            if action != "close":
                recovered = [event async for event in runtime.stream("retry")]
                assert isinstance(recovered[-1], TurnCompleted)
                assert len(requests) == 1
                assert [i.content for i in requests[0].items if isinstance(i, UserMessageItem)] == [
                    "retry"
                ]
            else:
                assert "model" in disposed
                with pytest.raises(RuntimeError, match="closed"):
                    await anext(runtime.stream("rejected"))
        finally:
            release.set()
            thread_release.set()
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            if closing is not None:
                await asyncio.gather(closing, return_exceptions=True)
            with suppress(asyncio.CancelledError):
                await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ["mcp", "checkpoint"])
@pytest.mark.parametrize("action", ["stop", "close"])
def test_cancellation_wins_if_startup_dependency_returns_after_cancellation(
    tmp_path, monkeypatch, boundary, action
):
    async def scenario():
        reached = asyncio.Event()
        events, requests = [], []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(working_directory=tmp_path, skills_enabled=False),
            database_path=tmp_path / "sessions.db",
            registry=ToolRegistry(),
            model=Model(),
        )

        async def returns_after_cancel(*args):
            reached.set()
            with suppress(asyncio.CancelledError):
                await asyncio.Event().wait()

        if boundary == "mcp":
            monkeypatch.setattr(runtime._mcp_manager, "start", returns_after_cancel)

            def unexpected_search_publication():
                pytest.fail("shutdown/cancellation must stop later startup stages")

            monkeypatch.setattr(runtime, "_sync_tool_search", unexpected_search_publication)
        else:
            monkeypatch.setattr(runtime_module.AsyncSqliteSaver, "setup", returns_after_cancel)

        async def consume():
            async for event in runtime.stream("cancelled before admission"):
                events.append(event)

        consumer = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(reached.wait(), 3)
            if action == "stop":
                await runtime.cancel_active()
            else:
                await runtime.aclose()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(consumer, 3)
            assert not events and not requests
            assert runtime._compiled is None and runtime._checkpointer is None
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())
