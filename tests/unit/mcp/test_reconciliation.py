import asyncio
from dataclasses import replace

import pytest

from corki.config import MCPServerSettings
from corki.mcp.client import MCPClient
from corki.mcp.connection import MCPServerMetadata
from corki.mcp.manager import MCPManager
from corki.mcp.reconciliation import connection_identity
from corki.mcp.request_policy import effective_timeout
from corki.tools import ToolRegistry


class Client(MCPClient):
    def __init__(self, settings):
        super().__init__(settings)
        self.starts = self.lists = self.closes = 0
        self.dead = False
        self.calls = []
        self.started = asyncio.Event()
        self.start_release = None
        self.close_entered = asyncio.Event()
        self.close_release = None
        self.server_instructions = "original instructions"
        self.names = ("read", "write")

    @property
    def is_closed(self):
        return self.dead or self.closes > 0

    async def start(self):
        self.starts += 1
        self.started.set()
        if self.start_release is not None:
            await self.start_release.wait()

    async def list_tools(self):
        self.lists += 1
        return tuple({"name": n, "inputSchema": {"type": "object"}} for n in self.names)

    async def call_tool(self, name, arguments):
        assert not self.is_closed
        self.calls.append((name, effective_timeout(self, self.settings.timeout_seconds)))
        return {"content": [{"type": "text", "text": name}]}

    async def aclose(self):
        self.closes += 1
        self.close_entered.set()
        if self.close_release is not None:
            await self.close_release.wait()

    async def list_resources(self, cursor=None):
        return {
            "resources": [
                {
                    "uri": "fixture://resources",
                    "name": "resources",
                    "timeout": effective_timeout(self, self.settings.timeout_seconds),
                }
            ]
        }

    async def _exchange(self, message):
        raise AssertionError(message)

    async def _send_notification(self, message):
        raise AssertionError(message)


def setting(**changes):
    return replace(MCPServerSettings("docs", "http", url="https://fixture.test"), **changes)


@pytest.mark.parametrize("required", [False, True])
def test_session_admission_and_named_call_do_not_wait_for_unrelated_startup(monkeypatch, required):
    async def scenario():
        release = asyncio.Event()
        clients = {}

        def factory(settings):
            client = Client(settings)
            if settings.name == "pending":
                client.start_release = release
            clients[settings.name] = client
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        registry = ToolRegistry()
        manager = MCPManager(
            (setting(name="pending"), setting(name="ready", required=required)), registry
        )
        try:
            await asyncio.wait_for(manager.start_session(), 1)
            result = await asyncio.wait_for(manager.call_tool("ready", "read", {}), 1)
            assert "read" in repr(result)
            assert not clients["pending"].lists
            await manager.capture_tools(optional_startup_grace_ms=1)
            generation = manager._preparation
            deadline = generation.deadline
            first_snapshot = registry.snapshot()
            await manager.capture_tools(optional_startup_grace_ms=1)
            assert manager._preparation is generation and generation.deadline == deadline
            assert not any("pending" in spec.name for spec in first_snapshot.specs())
            release.set()
            await asyncio.wait_for(manager.prepare_server("pending"), 1)
            assert any("pending" in spec.name for spec in registry.specs())
            assert not any("pending" in spec.name for spec in first_snapshot.specs())
            assert clients["pending"].starts == 1
        finally:
            await manager.aclose()
        assert all(c.closes == 1 for c in clients.values())

    asyncio.run(scenario())


@pytest.mark.parametrize("fail", [False, True])
def test_parallel_preparation_keeps_captured_order_despite_reverse_completion(monkeypatch, fail):
    async def scenario():
        second_started = asyncio.Event()
        clients, finished = [], []

        class OrderedClient(Client):
            async def start(self):
                await super().start()
                if self.settings.name == "z_first":
                    await second_started.wait()
                else:
                    second_started.set()

            async def list_tools(self):
                finished.append(self.settings.name)
                if fail:
                    raise ValueError("startup failure")
                return await super().list_tools()

        def factory(settings):
            client = OrderedClient(settings)
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        registry = ToolRegistry()
        manager = MCPManager((setting(name="z_first"), setting(name="a_second")), registry)
        try:
            await asyncio.wait_for(manager.start(), 1)
            assert finished == ["a_second", "z_first"]
            if fail:
                assert tuple(w.split(":", 1)[0] for w in manager.warnings) == (
                    "z_first",
                    "a_second",
                )
            else:
                assert tuple(manager._clients_by_name) == ("z_first", "a_second")
                assert len([s for s in registry.specs() if s.name.endswith("::read")]) == 2
        finally:
            await manager.aclose()
        assert len(clients) == 2 and all(c.closes == 1 for c in clients)

    asyncio.run(scenario())


def test_repeated_cancel_joins_all_startup_children_before_closing_connections(monkeypatch):
    async def scenario():
        entered = {name: asyncio.Event() for name in ("first", "second")}
        cleanup = {name: asyncio.Event() for name in entered}
        release = asyncio.Event()
        clients, recancelled = [], []

        class HeldClient(Client):
            async def start(self):
                name = self.settings.name
                entered[name].set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cleanup[name].set()
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        recancelled.append(name)
                        raise
                    raise

        def factory(settings):
            client = HeldClient(settings)
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        registry = ToolRegistry()
        manager = MCPManager(tuple(setting(name=name) for name in entered), registry)
        task = asyncio.create_task(manager.start())
        try:
            await asyncio.wait_for(asyncio.gather(*(e.wait() for e in entered.values())), 1)
            task.cancel()
            await asyncio.wait_for(asyncio.gather(*(e.wait() for e in cleanup.values())), 1)
            task.cancel()
            for _ in range(5):
                await asyncio.sleep(0)
            assert not task.done() and not recancelled
            assert all(c.closes == 0 for c in clients), "startup still owns each connection"
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            assert all(c.closes == 1 for c in clients)
            assert not registry.specs() and manager.refresh_pending
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await manager.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "changes,reuse",
    [
        ({}, True),
        ({"enabled_tools": ("read",)}, True),
        ({"disabled_tools": ("write",)}, True),
        ({"timeout_seconds": 80}, True),
        ({"tool_output_token_limits": (("read", 10),)}, True),
        ({"url": "https://new.test"}, False),
        ({"headers": (("Authorization", "fixture-only-token"),)}, False),
    ],
)
def test_policy_changes_reuse_but_transport_changes_reconnect(monkeypatch, changes, reuse):
    async def scenario():
        clients = []

        def factory(settings):
            client = Client(settings)
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        registry = ToolRegistry()
        original = setting()
        manager = MCPManager((original,), registry)
        try:
            await manager.start()
            registry.seal()
            previous = manager._clients_by_name["docs"]
            manager.request_reconcile((setting(**changes),))
            await manager.refresh_if_dirty()
            current = manager._clients_by_name["docs"]
            assert current is not previous
            assert (current.client is previous.client) == reuse
            assert previous.settings == original
            assert len(clients) == (1 if reuse else 2)
            assert all(c.starts == c.lists == 1 for c in clients)
            await manager.call_tool("docs", "read", {})
            assert current.client.calls[-1] == ("read", changes.get("timeout_seconds", 30))
        finally:
            await manager.aclose()
        assert all(c.closes == 1 for c in clients)

    asyncio.run(scenario())


def test_cached_unfiltered_catalog_and_instructions_survive_view_replacement(monkeypatch):
    async def scenario():
        original = setting(enabled_tools=("read",))
        client = Client(original)
        monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: client)
        registry = ToolRegistry()
        manager = MCPManager((original,), registry)
        try:
            await manager.start()
            client.names = ("new_tool",)
            client.server_instructions = "changed without catalog refresh"
            snapshot = manager._clients_by_name["docs"].definitions
            snapshot[0]["name"] = "mutated copy"
            manager.request_reconcile((setting(),))
            await manager.refresh_if_dirty()
            assert {s.name for s in registry.specs() if s.name.startswith("mcp__")} == {
                "mcp__docs::read",
                "mcp__docs::write",
            }
            assert registry.spec("mcp__docs::write").source_description == "original instructions"
            assert client.starts == client.lists == 1
        finally:
            await manager.aclose()
        assert client.closes == 1

    asyncio.run(scenario())


def test_old_admitted_preparation_retains_policy_while_new_view_uses_new_policy(monkeypatch):
    async def scenario():
        original = setting(timeout_seconds=40, tool_output_token_limits=(("write", 50),))
        latest = setting(
            timeout_seconds=70, tool_output_token_limits=(("read", 20),), enabled_tools=("read",)
        )
        client = Client(original)
        monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: client)
        manager = MCPManager((original,), ToolRegistry())
        await manager.start()
        started, release = asyncio.Event(), asyncio.Event()
        marks, limits = [], []

        async def mark():
            marks.append("old")
            started.set()
            await release.wait()

        async def forbidden_mark():
            raise AssertionError("new unpolluting view must not mark memory")

        old_call = asyncio.create_task(
            manager.call_tool(
                "docs", "write", {}, on_external_context=mark, on_output_token_limit=limits.append
            )
        )
        try:
            await asyncio.wait_for(started.wait(), 2)
            manager.request_reconcile((latest,), server_metadata={"docs": MCPServerMetadata(False)})
            await manager.refresh_if_dirty()
            await manager.call_tool(
                "docs",
                "read",
                {},
                on_external_context=forbidden_mark,
                on_output_token_limit=limits.append,
            )
            with pytest.raises(ValueError, match="disabled"):
                await manager.call_tool("docs", "write", {})
            assert client.closes == 0 and client.calls == [("read", 70)]
            release.set()
            await old_call
            assert client.calls == [("read", 70), ("write", 40)]
            assert limits == [50, 20] and marks == ["old"]
            assert client.settings == original
            assert effective_timeout(client, 123) == 123
        finally:
            release.set()
            await asyncio.gather(old_call, return_exceptions=True)
            await manager.aclose()
        assert client.closes == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["cancel", "normalize", "publish"])
def test_mixed_staging_failure_releases_reused_view_without_closing_published_client(
    monkeypatch, failure
):
    async def scenario():
        clients = []
        entered, release = asyncio.Event(), asyncio.Event()

        def factory(settings):
            client = Client(settings)
            clients.append(client)
            if settings.name == "new" and len(clients) == 2 and failure == "cancel":
                client.started = entered
                client.start_release = release
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        registry = ToolRegistry()
        manager = MCPManager((setting(),), registry)
        try:
            await manager.start()
            before = registry.specs()
            previous = manager._clients_by_name["docs"]
            desired = (setting(enabled_tools=()), setting(name="new"))
            manager.request_reconcile(desired)
            if failure == "cancel":
                task = asyncio.create_task(manager.refresh_if_dirty())
                await asyncio.wait_for(entered.wait(), 2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:

                def fail(*args, **kwargs):
                    raise ValueError("fixture failure")

                with monkeypatch.context() as patch:
                    if failure == "normalize":
                        patch.setattr("corki.mcp.manager.normalize_tool_names", fail)
                    else:
                        patch.setattr(registry, "replace_owned", fail)
                    with pytest.raises(ValueError, match="fixture failure"):
                        await manager.refresh_if_dirty()
            assert registry.specs() == before and manager._clients_by_name["docs"] is previous
            assert [c.closes for c in clients] == [0, 1] and manager.refresh_pending
            await manager.refresh_if_dirty()
            assert len(clients) == 3 and manager._clients_by_name["docs"].client is clients[0]
            with pytest.raises(ValueError, match="disabled"):
                await manager.call_tool("docs", "read", {})
        finally:
            release.set()
            await manager.aclose()
        assert all(c.closes == 1 for c in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("dead", [False, True])
def test_explicit_reconnect_is_not_lost_when_reconciliation_is_queued(monkeypatch, dead):
    async def scenario():
        clients = []

        def factory(settings):
            client = Client(settings)
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        manager = MCPManager((setting(),), ToolRegistry())
        try:
            await manager.start()
            clients[0].dead = dead
            if not dead:
                manager.request_refresh()
            manager.request_reconcile((setting(enabled_tools=("read",)),))
            await manager.refresh_if_dirty()
            assert len(clients) == 2
            assert manager._clients_by_name["docs"].client is clients[1]
        finally:
            await manager.aclose()
        assert all(c.closes == 1 for c in clients)

    asyncio.run(scenario())


def test_stdio_identity_tracks_referenced_environment_without_revealing_values(
    tmp_path, monkeypatch
):
    settings = MCPServerSettings("stdio", "stdio", command="fixture", cwd=tmp_path)
    monkeypatch.setenv("CORKI_RECONCILIATION_FIXTURE", "first-private-value")
    first = connection_identity(settings)
    monkeypatch.setenv("CORKI_RECONCILIATION_FIXTURE", "second-private-value")
    assert connection_identity(settings) == first
    explicit = replace(settings, env=(("CORKI_RECONCILIATION_FIXTURE", "fixed"),))
    captured = connection_identity(explicit)
    monkeypatch.setenv("CORKI_RECONCILIATION_FIXTURE", "third-private-value")
    assert connection_identity(explicit) == captured
    referenced = replace(explicit, env_vars=("CORKI_RECONCILIATION_FIXTURE",))
    reference_identity = connection_identity(referenced)
    monkeypatch.setenv("CORKI_RECONCILIATION_FIXTURE", "fourth-private-value")
    assert connection_identity(referenced) != reference_identity
    assert len(first) == 64 and "private" not in first
    for changed in (
        replace(explicit, args=("new",)),
        replace(explicit, command="other"),
        replace(explicit, cwd=tmp_path / "other"),
    ):
        assert connection_identity(changed) != captured


def test_aggregate_resource_leases_keep_each_servers_current_timeout(monkeypatch):
    async def scenario():
        clients = []

        def factory(settings):
            client = Client(settings)
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        manager = MCPManager((setting(), setting(name="notes")), ToolRegistry())
        try:
            await manager.start()
            manager.request_reconcile(
                (setting(timeout_seconds=70), setting(name="notes", timeout_seconds=80))
            )
            await manager.refresh_if_dirty()
            assert await manager.list_capability("resources", None) == {
                "resources": [
                    {
                        "server": "docs",
                        "uri": "fixture://resources",
                        "name": "resources",
                        "timeout": 70,
                    },
                    {
                        "server": "notes",
                        "uri": "fixture://resources",
                        "name": "resources",
                        "timeout": 80,
                    },
                ],
            }
            assert len(clients) == 2
        finally:
            await manager.aclose()
        assert all(c.closes == 1 for c in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("close_error", [None, RuntimeError, asyncio.CancelledError])
def test_shutdown_cancels_old_view_users_and_retains_shared_close_after_waiter_cancel(
    monkeypatch, close_error
):
    async def scenario():
        class ClosingClient(Client):
            async def aclose(self):
                await super().aclose()
                if close_error is not None and self.settings.name == "docs":
                    raise close_error("physical transport close failed")

        client = ClosingClient(setting())
        sibling = ClosingClient(setting(name="notes"))
        client.close_release = asyncio.Event()
        monkeypatch.setattr(
            "corki.mcp.manager.create_client",
            lambda settings: client if settings.name == "docs" else sibling,
        )
        manager = MCPManager((setting(), setting(name="notes")), ToolRegistry())
        await manager.start()
        entered = asyncio.Event()

        async def mark():
            entered.set()
            await asyncio.Event().wait()

        call = asyncio.create_task(manager.call_tool("docs", "read", {}, on_external_context=mark))
        closer = None
        try:
            await asyncio.wait_for(entered.wait(), 2)
            manager.request_reconcile((setting(timeout_seconds=80), setting(name="notes")))
            await manager.refresh_if_dirty()
            closer = asyncio.create_task(manager.aclose())
            await asyncio.wait_for(client.close_entered.wait(), 2)
            closer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closer
            assert call.cancelled() and client.closes == 1 and not manager._shutdown_task.done()
            client.close_release.set()
            for _ in range(2):
                if close_error is None:
                    await manager.aclose()
                else:
                    with pytest.raises(close_error):
                        await manager.aclose()
            assert not manager._retired and not manager._clients_by_name
            assert client.closes == sibling.closes == 1
        finally:
            client.close_release.set()
            call.cancel()
            await asyncio.gather(call, return_exceptions=True)
            if closer is not None:
                await asyncio.gather(closer, return_exceptions=True)
            await asyncio.gather(manager.aclose(), return_exceptions=True)
        assert client.closes == 1

    asyncio.run(scenario())


def test_force_reconnect_arriving_during_reconciliation_survives_later_config_update(monkeypatch):
    async def scenario():
        clients = []
        entered, release = asyncio.Event(), asyncio.Event()

        def factory(settings):
            client = Client(settings)
            clients.append(client)
            if settings.name == "new":
                client.started, client.start_release = entered, release
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        manager = MCPManager((setting(),), ToolRegistry())
        await manager.start()
        manager.request_reconcile((setting(), setting(name="new")))
        task = asyncio.create_task(manager.refresh_if_dirty())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            manager.request_refresh()
            manager.request_reconcile((setting(enabled_tools=("read",)),))
            release.set()
            await task
            assert len(clients) == 3 and manager._clients_by_name["docs"].client is clients[2]
            assert set(manager._clients_by_name) == {"docs"} and not manager.refresh_pending
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await manager.aclose()
        assert all(c.closes == 1 for c in clients)

    asyncio.run(scenario())


def test_cancelled_force_reconnect_restores_force_even_after_new_reconcile_request(monkeypatch):
    async def scenario():
        clients = []
        entered, release = asyncio.Event(), asyncio.Event()

        def factory(settings):
            client = Client(settings)
            clients.append(client)
            if len(clients) == 2:
                client.started, client.start_release = entered, release
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        manager = MCPManager((setting(),), ToolRegistry())
        await manager.start()
        manager.request_refresh()
        task = asyncio.create_task(manager.refresh_if_dirty())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            manager.request_reconcile((setting(enabled_tools=("read",)),))
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert clients[0].closes == 0 and clients[1].closes == 1
            await manager.refresh_if_dirty()
            assert len(clients) == 3 and manager._clients_by_name["docs"].client is clients[2]
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await manager.aclose()
        assert all(c.closes == 1 for c in clients)

    asyncio.run(scenario())
