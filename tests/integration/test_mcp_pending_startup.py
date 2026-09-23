"""Pending optional MCP startup belongs to the session, not Turn admission."""

import asyncio

import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import MCPClient
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted, TurnStarted
from corki.protocol.ids import ToolCallId
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("value", [-1, True, None, 1.5, 2**64, []])
def test_startup_grace_requires_uint64(tmp_path, value):
    with pytest.raises(ValueError, match="mcp_optional_startup_grace_ms"):
        CorkiSettings(working_directory=tmp_path, mcp_optional_startup_grace_ms=value)


@pytest.mark.parametrize("value", [0, 10, 1000])
def test_native_top_level_startup_grace_configuration(tmp_path, value):
    config = tmp_path / "config.toml"
    config.write_text(f"mcp_optional_startup_grace_ms = {value}\n")
    settings = CorkiSettings.for_directory(tmp_path, config_file=config)
    assert settings.mcp_optional_startup_grace_ms == value


class PendingClient(MCPClient):
    def __init__(self, settings):
        super().__init__(settings)
        self.entered, self.release, self.listed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.closed = False
        self.calls = []

    async def start(self):
        pass

    async def list_tools(self):
        self.entered.set()
        await self.release.wait()
        self.listed.set()
        return [{"name": "lookup", "inputSchema": {"type": "object"}}]

    async def call_tool(self, name, arguments):
        self.calls.append(name)
        return {"content": [{"type": "text", "text": "found"}]}

    async def aclose(self):
        self.closed = True

    async def _exchange(self, message):
        raise AssertionError(message)

    async def _send_notification(self, message):
        raise AssertionError(message)


async def make_runtime(tmp_path, monkeypatch, model, **options):
    server = MCPServerSettings("pending", "http", url="https://pending.test")
    client = PendingClient(server)
    monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: client)
    runtime = await LangGraphRuntime.acreate(
        settings=CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            execution_permissions=None,
            mcp_servers=(server,),
            tool_search_mode="disabled",
            **options,
        ),
        database_path=tmp_path / "sessions.db",
        model=model,
        registry=ToolRegistry(),
    )
    return runtime, client


def test_default_turn_started_does_not_wait_for_optional_mcp(tmp_path, monkeypatch):
    async def scenario():
        class Model:
            async def stream(self, request):
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime, client = await make_runtime(tmp_path, monkeypatch, Model())
        stream = runtime.stream("start now")
        try:
            event = await asyncio.wait_for(anext(stream), 1)
            assert isinstance(event, TurnStarted)
            assert not client.listed.is_set()
        finally:
            await stream.aclose()
            await runtime.aclose()
        assert client.closed

    asyncio.run(scenario())


def test_active_turn_cancel_retains_pending_startup_until_idle_interrupt(tmp_path, monkeypatch):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime, client = await make_runtime(
            tmp_path, monkeypatch, Model(), mcp_optional_startup_grace_ms=0
        )
        events = []
        started = asyncio.Event()

        async def consume():
            async for event in runtime.stream("wait"):
                events.append(event)
                if isinstance(event, TurnStarted):
                    started.set()

        consumer = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(started.wait(), 1)
            await asyncio.wait_for(client.entered.wait(), 1)
            generation = runtime._mcp_manager._preparation
            await runtime.cancel_active()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(consumer, 1)
            assert not client.closed and not requests
            assert any(not task.done() for task in generation.tasks.values())
            assert runtime._active_run is None
            await runtime.cancel_active()
            await asyncio.wait_for(
                asyncio.gather(*generation.tasks.values(), return_exceptions=True), 1
            )
            assert client.closed
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            await runtime.aclose()

    asyncio.run(scenario())


def test_failed_session_setup_joins_unpublished_optional_startup(tmp_path, monkeypatch):
    async def scenario():
        from corki.core import runtime as runtime_module

        class Model:
            async def stream(self, request):
                raise AssertionError("failed initialization must not sample")
                yield

            async def aclose(self):
                pass

        runtime, client = await make_runtime(tmp_path, monkeypatch, Model())
        original = runtime_module.setup_checkpoint

        async def fail_setup(checkpointer):
            await original(checkpointer)
            await client.entered.wait()
            raise ValueError("checkpoint setup failed")

        monkeypatch.setattr(runtime_module, "setup_checkpoint", fail_setup)
        try:
            with pytest.raises(ValueError, match="checkpoint setup failed"):
                await asyncio.wait_for(runtime._ensure_ready(), 1)
            assert client.closed
            assert runtime._compiled is None and runtime._mcp_manager._preparation is None
            assert runtime._mcp_manager.refresh_pending
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_cold_step_rebind_waits_saved_servers_not_new_unrelated_startup(tmp_path, monkeypatch):
    async def scenario():
        from corki.core.graph import GraphRunContext
        from corki.core.runtime import _initial_state
        from corki.protocol.ids import new_turn_id
        from corki.protocol.items import UserMessageItem
        from corki.sessions import TurnRecord, TurnStatus

        first_request = asyncio.Event()
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                first_request.set()
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(ToolCallId("restored"), "mcp__pending::lookup", {}),
                                turn,
                                step,
                            ),
                        )
                    )
                else:
                    assert any(
                        isinstance(i, ToolResultItem) and "found" in i.content
                        for i in request.items
                    )
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        class Sink:
            async def emit(self, event):
                pass

        old, first_client = await make_runtime(tmp_path, monkeypatch, Model())
        first_client.release.set()
        turn = new_turn_id()
        try:
            await old._ensure_ready()
            user = UserMessageItem("once", turn)
            await old._repository.save_turn(
                TurnRecord(turn, old.thread_id, TurnStatus.RUNNING, "once")
            )
            await old._compiled.ainvoke(
                _initial_state(old.thread_id, turn, old._settings, user),
                config=old._graph_config(turn),
                context=GraphRunContext(events=Sink()),
                interrupt_before=["call_model"],
            )
            checkpoint = await old._compiled.aget_state(old._graph_config(turn))
            assert tuple(checkpoint.values["tool_snapshot_mcp_servers"]) == ("pending",)
        finally:
            await old.aclose()

        clients = {}

        def factory(settings):
            client = PendingClient(settings)
            if settings.name == "pending":
                client.release.set()
            clients[settings.name] = client
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        cold = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                execution_permissions=None,
                tool_search_mode="disabled",
                mcp_optional_startup_grace_ms=10,
                mcp_servers=tuple(
                    MCPServerSettings(name, "http", url=f"https://{name}.test")
                    for name in ("pending", "unrelated")
                ),
            ),
            database_path=tmp_path / "sessions.db",
            thread_id=old.thread_id,
            model=Model(),
            registry=ToolRegistry(),
        )

        async def consume():
            return [e async for e in cold.resume_pending()]

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(first_request.wait(), 1)
            assert not clients["unrelated"].listed.is_set()
            events = await asyncio.wait_for(task, 2)
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert clients["pending"].calls == ["lookup"] and len(requests) == 2
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await cold.aclose()
        assert all(client.closed for client in clients.values())

    asyncio.run(scenario())


def test_optional_grace_omits_pending_then_loads_ready_tool_next_step(tmp_path, monkeypatch):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                turn, step = request.items[-1].turn_id, new_step_id()
                if len(requests) == 1:
                    assert not any("pending" in t.name for t in request.tools)
                    client.release.set()
                    await client.listed.wait()
                    yield ModelCompleted(
                        (AssistantMessageItem("continue", turn, step),), end_turn=False
                    )
                elif len(requests) == 2:
                    tool = next(t for t in request.tools if t.name.endswith("::lookup"))
                    yield ModelCompleted(
                        (ToolCallItem(ToolCall(ToolCallId("lookup"), tool.name, {}), turn, step),)
                    )
                else:
                    assert any(
                        isinstance(i, ToolResultItem) and "found" in i.content
                        for i in request.items
                    )
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        runtime, client = await make_runtime(
            tmp_path, monkeypatch, Model(), mcp_optional_startup_grace_ms=10
        )
        try:

            async def consume():
                return [event async for event in runtime.stream("find it")]

            events = await asyncio.wait_for(consume(), 2)
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 3 and client.calls == ["lookup"]
            assert not any("pending" in t.name for t in requests[0].tools)
        finally:
            await runtime.aclose()
        assert client.closed

    asyncio.run(scenario())


def test_zero_grace_waits_for_server_but_not_turn_started(tmp_path, monkeypatch):
    async def scenario():
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime, client = await make_runtime(
            tmp_path, monkeypatch, Model(), mcp_optional_startup_grace_ms=0
        )
        stream = runtime.stream("wait for tools")
        waiter = None
        try:
            assert isinstance(await asyncio.wait_for(anext(stream), 1), TurnStarted)

            async def consume():
                return [event async for event in stream]

            waiter = asyncio.create_task(consume())
            await asyncio.wait_for(client.entered.wait(), 1)
            for _ in range(10):
                await asyncio.sleep(0)
            assert not requests and not waiter.done()
            client.release.set()
            result = await asyncio.wait_for(waiter, 2)
            assert isinstance(result[-1], TurnCompleted)
            assert any(t.name.endswith("::lookup") for t in requests[0].tools)
        finally:
            if waiter is not None:
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
            await stream.aclose()
            await runtime.aclose()

    asyncio.run(scenario())
