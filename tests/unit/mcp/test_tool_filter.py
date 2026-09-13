import asyncio
from dataclasses import replace

import pytest

from corki.config import MCPServerSettings
from corki.mcp.client import MCPClient
from corki.mcp.manager import MCPManager
from corki.tools import ToolRegistry


class FilterClient(MCPClient):
    def __init__(self, settings):
        super().__init__(settings)
        self.calls = []
        self.entered = asyncio.Event()
        self.started = asyncio.Event()
        self.closed = asyncio.Event()
        self.release = None
        self.start_release = None

    async def start(self):
        self.started.set()
        if self.start_release is not None:
            await self.start_release.wait()

    async def list_tools(self):
        return tuple(
            {"name": name, "inputSchema": {"type": "object"}}
            for name in ("read-file", "read_file", "write")
        )

    async def list_resources(self, cursor=None):
        return {"resources": [{"uri": "fixture://resource", "name": "resource"}]}

    async def call_tool(self, name, arguments):
        self.calls.append(name)
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        assert not self.closed.is_set()
        return {"content": [{"type": "text", "text": name}]}

    async def aclose(self):
        self.closed.set()

    async def _exchange(self, message):
        raise AssertionError(message)

    async def _send_notification(self, message):
        raise AssertionError(message)


def setting(**policy):
    return MCPServerSettings.from_mapping(
        "docs",
        {
            "transport": "http",
            "url": "https://fixture.test",
            **policy,
        },
    )


@pytest.mark.parametrize("deferred", [False, True])
@pytest.mark.parametrize(
    "enabled,disabled,expected",
    [
        (None, None, {"read-file", "read_file", "write"}),
        ([], None, set()),
        (["read-file", "read-file", "write"], ["write"], {"read-file"}),
        (None, ["read-file", "write"], {"read_file"}),
        (["Read-file", " read_file", "mcp__docs::write", "missing"], [], set()),
        (["read-file"], ["read-file"], set()),
        (None, [], {"read-file", "read_file", "write"}),
    ],
)
def test_catalog_and_raw_admission_use_same_filter(
    monkeypatch, deferred, enabled, disabled, expected
):
    async def scenario():
        settings = setting(enabled_tools=enabled, disabled_tools=disabled)
        client = FilterClient(settings)
        monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: client)
        registry = ToolRegistry()
        manager = MCPManager((settings,), registry, defer_tools=deferred)
        try:
            await manager.start()
            assert not manager.warnings
            tools = [registry.get(s.name) for s in registry.specs() if s.name.startswith("mcp__")]
            assert {t.remote_name for t in tools} == expected
            assert all(t.spec.exposure.is_deferred == deferred for t in tools)
            if expected == {"read-file"} or expected == {"read_file"}:
                assert [t.spec.name for t in tools] == ["mcp__docs::read_file"]
            callbacks = []

            async def external():
                callbacks.append("external")

            for name in ("read-file", "read_file", "write"):
                if name in expected:
                    await manager.call_tool("docs", name, {}, on_external_context=external)
                else:
                    with pytest.raises(ValueError, match="disabled"):
                        await manager.call_tool("docs", name, {}, on_external_context=external)
            assert set(client.calls) == expected
            assert callbacks == ["external"] * len(expected)
            # Tool policy is not a server-wide resource access switch.
            assert await manager.list_capability("resources", "docs") == {
                "server": "docs",
                "resources": [{"server": "docs", "uri": "fixture://resource", "name": "resource"}],
            }
        finally:
            await manager.aclose()
        assert client.closed.is_set()

    asyncio.run(scenario())


def test_server_filters_precede_global_namespace_collision_resolution(monkeypatch):
    async def scenario():
        settings = (
            replace(setting(enabled_tools=[]), name="basic-server"),
            replace(setting(enabled_tools=["write"]), name="basic_server"),
        )
        clients = []

        def factory(setting):
            client = FilterClient(setting)
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        registry = ToolRegistry()
        manager = MCPManager(settings, registry)
        try:
            await manager.start()
            assert [s.name for s in registry.specs() if s.name.startswith("mcp__")] == [
                "mcp__basic_server::write"
            ]
            with pytest.raises(ValueError, match="disabled"):
                await manager.call_tool("basic-server", "write", {})
            await manager.call_tool("basic_server", "write", {})
            assert [c.calls for c in clients] == [[], ["write"]]
        finally:
            await manager.aclose()

    asyncio.run(scenario())


def test_policy_revocation_keeps_admitted_call_but_rejects_new_call(monkeypatch):
    async def scenario():
        settings, restricted = setting(), setting(disabled_tools=["write"])
        first, second = FilterClient(settings), FilterClient(restricted)
        first.release = asyncio.Event()
        clients = iter((first, second))
        monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: next(clients))
        registry = ToolRegistry()
        manager = MCPManager((settings,), registry)
        await manager.start()
        registry.seal()
        running = asyncio.create_task(manager.call_tool("docs", "write", {}))
        try:
            await asyncio.wait_for(first.entered.wait(), 2)
            manager.request_refresh((restricted,))
            with pytest.raises(ValueError, match="disabled"):
                await manager.call_tool("docs", "write", {})
            assert not first.closed.is_set() and second.calls == []
            first.release.set()
            assert await running == {"content": [{"type": "text", "text": "write"}]}
            await asyncio.wait_for(first.closed.wait(), 2)
            assert first.calls == ["write"]
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
            await manager.aclose()
        assert second.closed.is_set()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["cancel", "publish"])
def test_failed_refresh_keeps_published_policy_and_retries_new_policy(monkeypatch, failure):
    async def scenario():
        settings, restricted = setting(), setting(enabled_tools=[])
        first, staged, retry = (
            FilterClient(settings),
            FilterClient(restricted),
            FilterClient(restricted),
        )
        if failure == "cancel":
            staged.start_release = asyncio.Event()
        clients = iter((first, staged, retry))
        monkeypatch.setattr("corki.mcp.manager.create_client", lambda _: next(clients))
        registry = ToolRegistry()
        manager = MCPManager((settings,), registry)
        try:
            await manager.start()
            registry.seal()
            old = manager._clients_by_name["docs"]
            before = registry.specs()
            manager.request_refresh((restricted,))
            if failure == "cancel":
                task = asyncio.create_task(manager.refresh_if_dirty())
                await asyncio.wait_for(staged.started.wait(), 2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                with monkeypatch.context() as patch:

                    def fail(*args):
                        raise ValueError("fixture publish failure")

                    patch.setattr(registry, "replace_owned", fail)
                    with pytest.raises(ValueError, match="fixture publish failure"):
                        await manager.refresh_if_dirty()
            assert manager._clients_by_name["docs"] is old
            assert old.tool_filter.allows("write") and registry.specs() == before
            assert staged.closed.is_set() and not first.closed.is_set()
            assert manager.refresh_pending
            with pytest.raises(ValueError, match="disabled"):
                await manager.call_tool("docs", "write", {})
            assert not manager.refresh_pending and retry.calls == first.calls == []
            assert not any(s.name.startswith("mcp__") for s in registry.specs())
        finally:
            await manager.aclose()
        assert all(c.closed.is_set() for c in (first, staged, retry))

    asyncio.run(scenario())
