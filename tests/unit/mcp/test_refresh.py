import asyncio

import pytest

from corki.config import MCPServerSettings
from corki.mcp.client import MCPClient
from corki.mcp.manager import MCPManager
from corki.protocol.ids import ToolCallId
from corki.protocol.tools import ToolCall
from corki.tools import ToolContext, ToolExecutor, ToolRegistry


class RefreshClient(MCPClient):
    def __init__(self, settings, version):
        super().__init__(settings)
        self.version = version
        self.closed = asyncio.Event()
        self.started = asyncio.Event()
        self.entered = asyncio.Event()
        self.release = None
        self.start_release = None
        self.fail = False
        self.calls = 0

    async def start(self):
        self.started.set()
        if self.start_release is not None:
            await self.start_release.wait()
        if self.fail:
            raise ValueError("fixture server unavailable")

    async def list_tools(self):
        return ({"name": "lookup", "description": self.version, "inputSchema": {"type": "object"}},)

    async def call_tool(self, name, arguments):
        self.calls += 1
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        assert not self.closed.is_set()
        return {"content": [{"type": "text", "text": self.version}]}

    async def _exchange(self, message):
        raise AssertionError(message)

    async def _send_notification(self, message):
        raise AssertionError(message)

    async def aclose(self):
        self.closed.set()


@pytest.mark.parametrize("failure", ["normalize", "publish"])
def test_canonical_catalog_failure_preserves_previous_generation_and_closes_staging(
    monkeypatch, failure
):
    async def scenario():
        settings = MCPServerSettings("docs", "http", url="https://fixture.test")
        first, failed, retried = [
            RefreshClient(settings, word) for word in ("first", "failed", "retried")
        ]
        clients = iter((first, failed, retried))
        monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: next(clients))
        registry = ToolRegistry()
        manager = MCPManager((settings,), registry)
        try:
            await manager.start()
            registry.seal()
            previous = registry.specs()
            original = registry.get("mcp__docs::lookup")
            manager.request_refresh()

            def fail(*args, **kwargs):
                raise ValueError("catalog publication fixture")

            with monkeypatch.context() as patch:
                if failure == "normalize":
                    patch.setattr("corki.mcp.manager.normalize_tool_names", fail)
                else:
                    patch.setattr(registry, "replace_owned", fail)
                with pytest.raises(ValueError, match="catalog publication fixture"):
                    await manager.refresh_if_dirty()
            assert registry.specs() == previous
            assert registry.get("mcp__docs::lookup") is original
            assert not first.closed.is_set() and failed.closed.is_set()
            assert manager.refresh_pending
            await manager.refresh_if_dirty()
            assert registry.spec("mcp__docs::lookup").description == "retried"
            assert original.spec.description == "first"
        finally:
            await manager.aclose()
        assert all(client.closed.is_set() for client in (first, failed, retried))

    asyncio.run(scenario())


def test_refresh_keeps_exact_in_flight_client_until_call_finishes(tmp_path, monkeypatch):
    async def scenario():
        settings = MCPServerSettings("docs", "http", url="https://fixture.test")
        first, second = (RefreshClient(settings, word) for word in ("amber", "cobalt"))
        first.release = asyncio.Event()
        clients = iter((first, second))
        monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: next(clients))
        registry = ToolRegistry()
        manager = MCPManager((settings,), registry)
        await manager.start()
        registry.seal()
        executor = ToolExecutor(registry, output_char_budget=1000)
        call = asyncio.create_task(
            executor.execute(
                ToolCall(ToolCallId("old"), "mcp__docs::lookup", {}), ToolContext(cwd=tmp_path)
            )
        )
        try:
            await first.entered.wait()
            manager.request_refresh()
            await manager.refresh_if_dirty()
            assert registry.spec("mcp__docs::lookup").description == "cobalt"
            assert not first.closed.is_set() and not second.closed.is_set()
            first.release.set()
            assert (await call).content.split("\nOutput:\n", 1)[1] == "amber"
            await asyncio.wait_for(first.closed.wait(), 1)
            assert first.calls == 1 and second.calls == 0
            fresh = await executor.execute(
                ToolCall(ToolCallId("new"), "mcp__docs::lookup", {}), ToolContext(cwd=tmp_path)
            )
            assert fresh.content.split("\nOutput:\n", 1)[1] == "cobalt" and second.calls == 1
        finally:
            call.cancel()
            await asyncio.gather(call, return_exceptions=True)
            await manager.aclose()
        assert second.closed.is_set()

    asyncio.run(scenario())


def test_cancelled_refresh_retains_old_publication_and_pending_retry(monkeypatch):
    async def scenario():
        settings = MCPServerSettings("docs", "http", url="https://fixture.test")
        first, blocked, retry = (
            RefreshClient(settings, word) for word in ("amber", "bad", "cobalt")
        )
        blocked.start_release = asyncio.Event()
        clients = iter((first, blocked, retry))
        monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: next(clients))
        registry = ToolRegistry()
        manager = MCPManager((settings,), registry)
        await manager.start()
        registry.seal()
        before = registry.specs()
        manager.request_refresh()
        task = asyncio.create_task(manager.refresh_if_dirty())
        await blocked.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert blocked.closed.is_set() and not first.closed.is_set()
        assert registry.specs() == before and manager.refresh_pending
        await manager.refresh_if_dirty()
        assert not manager.refresh_pending
        assert registry.spec("mcp__docs::lookup").description == "cobalt"
        await manager.aclose()
        assert first.closed.is_set() and retry.closed.is_set()

    asyncio.run(scenario())


def test_publication_failure_rolls_back_and_can_retry(monkeypatch):
    async def scenario():
        settings = MCPServerSettings("docs", "http", url="https://fixture.test")
        clients = [RefreshClient(settings, word) for word in ("amber", "discard", "cobalt")]
        factory = iter(clients)
        monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: next(factory))
        registry = ToolRegistry()
        manager = MCPManager((settings,), registry)
        await manager.start()
        registry.seal()
        before = registry.specs()
        manager.request_refresh()
        with monkeypatch.context() as patch:

            def fail(*args):
                raise ValueError("fixture publication failed")

            patch.setattr(registry, "replace_owned", fail)
            with pytest.raises(ValueError, match="publication failed"):
                await manager.refresh_if_dirty()
        assert manager.refresh_pending and registry.specs() == before
        assert clients[1].closed.is_set() and not clients[0].closed.is_set()
        await manager.refresh_if_dirty()
        assert registry.spec("mcp__docs::lookup").description == "cobalt"
        await manager.aclose()
        assert all(c.closed.is_set() for c in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("construction_failure", [False, True])
def test_optional_server_failure_publishes_other_sources(monkeypatch, construction_failure):
    async def scenario():
        settings = tuple(
            MCPServerSettings(name, "http", url="https://fixture.test")
            for name in ("docs", "notes")
        )
        clients = []
        failing = False

        def factory(setting):
            if failing and setting.name == "docs" and construction_failure:
                raise ValueError("fixture construction failed")
            client = RefreshClient(setting, "cobalt" if failing else "amber")
            client.fail = failing and setting.name == "docs"
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        registry = ToolRegistry()
        manager = MCPManager(settings, registry)
        await manager.start()
        registry.seal()
        failing = True
        manager.request_refresh()
        await manager.refresh_if_dirty()
        assert registry.get("mcp__docs::lookup") is None
        assert registry.spec("mcp__notes::lookup").description == "cobalt"
        assert len(manager.warnings) == 1 and "docs:" in manager.warnings[0]
        manager.request_refresh(())
        await manager.refresh_if_dirty()
        assert registry.specs() == () and manager.tool_names == ()
        await manager.aclose()
        assert all(c.closed.is_set() for c in clients)

    asyncio.run(scenario())


def test_refresh_during_refresh_uses_latest_desired_state_and_serializes_waiters(monkeypatch):
    async def scenario():
        first_settings = MCPServerSettings("docs", "http", url="https://fixture.test")
        next_settings = MCPServerSettings("notes", "http", url="https://fixture.test")
        first = RefreshClient(first_settings, "amber")
        first.start_release = asyncio.Event()
        second = RefreshClient(next_settings, "cobalt")
        clients = iter((first, second))
        monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: next(clients))
        registry = ToolRegistry()
        manager = MCPManager((first_settings,), registry)
        task = asyncio.create_task(manager.start())
        await first.started.wait()
        manager.request_refresh((next_settings,))
        waiter = asyncio.create_task(manager.refresh_if_dirty())
        first.start_release.set()
        await asyncio.gather(task, waiter)
        assert registry.get("mcp__docs::lookup") is None
        assert registry.spec("mcp__notes::lookup").description == "cobalt"
        assert not manager.refresh_pending
        await manager.aclose()
        assert first.closed.is_set() and second.closed.is_set()

    asyncio.run(scenario())


def test_shutdown_cancels_old_in_flight_call_and_joins_all_connections(tmp_path, monkeypatch):
    async def scenario():
        settings = MCPServerSettings("docs", "http", url="https://fixture.test")
        first, second = (RefreshClient(settings, word) for word in ("amber", "cobalt"))
        first.release = asyncio.Event()
        clients = iter((first, second))
        monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: next(clients))
        registry = ToolRegistry()
        manager = MCPManager((settings,), registry)
        await manager.start()
        registry.seal()
        executor = ToolExecutor(registry, output_char_budget=1000)
        task = asyncio.create_task(
            executor.execute(
                ToolCall(ToolCallId("old"), "mcp__docs::lookup", {}), ToolContext(cwd=tmp_path)
            )
        )
        await first.entered.wait()
        manager.request_refresh()
        await manager.refresh_if_dirty()
        await manager.aclose()
        assert task.cancelled() and first.calls == 1 and second.calls == 0
        assert first.closed.is_set() and second.closed.is_set()
        with pytest.raises(RuntimeError, match="closed"):
            manager.request_refresh()

    asyncio.run(scenario())


def test_cancelled_close_waiter_does_not_abandon_owned_shutdown(monkeypatch):
    async def scenario():
        class ClosingClient(RefreshClient):
            async def aclose(self):
                closing.set()
                await finish.wait()
                await super().aclose()

        closing, finish = asyncio.Event(), asyncio.Event()
        settings = MCPServerSettings("docs", "http", url="https://fixture.test")
        client = ClosingClient(settings, "amber")
        monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: client)
        manager = MCPManager((settings,), ToolRegistry())
        await manager.start()
        waiter = asyncio.create_task(manager.aclose())
        await closing.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not client.closed.is_set()
        finish.set()
        await manager.aclose()
        assert client.closed.is_set()

    asyncio.run(scenario())


def test_aggregate_resource_call_captures_all_clients_before_refresh(monkeypatch):
    async def scenario():
        class ResourceClient(RefreshClient):
            async def list_resources(self, cursor=None):
                if self.version == "amber" and self.settings.name == "docs":
                    entered.set()
                    await release.wait()
                assert not self.closed.is_set()
                return {"resources": [{"uri": self.version, "name": self.version}]}

        entered, release = asyncio.Event(), asyncio.Event()
        settings = tuple(
            MCPServerSettings(name, "http", url="https://fixture.test")
            for name in ("docs", "notes")
        )
        clients = []

        def factory(setting):
            client = ResourceClient(setting, "amber" if len(clients) < 2 else "cobalt")
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        registry = ToolRegistry()
        manager = MCPManager(settings, registry)
        await manager.start()
        registry.seal()
        task = asyncio.create_task(manager.list_capability("resources", None))
        await entered.wait()
        manager.request_refresh()
        await manager.refresh_if_dirty()
        assert not any(c.closed.is_set() for c in clients)
        release.set()
        assert await task == {
            "resources": [
                {"server": "docs", "uri": "amber", "name": "amber"},
                {"server": "notes", "uri": "amber", "name": "amber"},
            ]
        }
        await manager.aclose()
        assert all(c.closed.is_set() for c in clients)

    asyncio.run(scenario())


def test_not_yet_admitted_tool_call_resolves_refreshed_exact_client(tmp_path, monkeypatch):
    async def scenario():
        settings = MCPServerSettings("docs", "http", url="https://fixture.test")
        first, second = (RefreshClient(settings, word) for word in ("amber", "cobalt"))
        clients = iter((first, second))
        monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: next(clients))
        registry = ToolRegistry()
        manager = MCPManager((settings,), registry)
        await manager.start()
        registry.seal()
        frozen_spec = registry.spec("mcp__docs::lookup")
        manager.request_refresh()
        # No explicit refresh_if_dirty call: tool admission itself must drain it.
        result = await ToolExecutor(registry, output_char_budget=1000).execute(
            ToolCall(ToolCallId("after-invalidation"), frozen_spec.name, {}),
            ToolContext(cwd=tmp_path),
            spec=frozen_spec,
        )
        assert not result.is_error and result.content.split("\nOutput:\n", 1)[1] == "cobalt"
        assert first.calls == 0 and second.calls == 1
        assert not manager.refresh_pending
        await manager.aclose()
        assert first.closed.is_set() and second.closed.is_set()

    asyncio.run(scenario())
