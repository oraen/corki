"""Pinned host HTTP capabilities run through the real Runtime, not a fake MCP client."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from test_mcp_tool_approval import Model

from corki.config import CorkiSettings, MCPServerSettings
from corki.config.mcp_requirements import MCPRequirements
from corki.core import LangGraphRuntime
from corki.mcp.catalog import MCPCatalog, MCPCatalogSource, MCPRegistration
from corki.mcp.client import HttpMCPClient, MCPProtocolError
from corki.mcp.manager import MCPManager
from corki.mcp.runtime_environment import MCPHTTPEnvironment, MCPRuntimeContext
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ToolResultItem
from corki.tools import ToolRegistry


class Body(httpx.AsyncByteStream):
    def __init__(self, data):
        self.data = data
        self.closed = False

    async def __aiter__(self):
        midpoint = len(self.data) // 2
        yield self.data[:midpoint]
        yield self.data[midpoint:]

    async def aclose(self):
        self.closed = True


class Carrier(httpx.AsyncBaseTransport):
    def __init__(self, label="first", expire=False):
        self.label, self.expire = label, expire
        self.requests, self.calls, self.bodies = [], [], []
        self.handshakes = 0
        self.closed = False
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.release.set()

    async def handle_async_request(self, request):
        assert not self.closed
        self.requests.append(request)
        if request.method == "GET":
            return httpx.Response(405)
        if request.method == "DELETE":
            return httpx.Response(204)
        message = json.loads(request.content)
        if "id" not in message:
            return httpx.Response(202)
        method = message["method"]
        if method == "initialize":
            self.handshakes += 1
            result = {
                "capabilities": {},
                "serverInfo": {"name": self.label, "version": "1"},
                "protocolVersion": "2025-06-18",
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {"name": "write", "description": "needle", "inputSchema": {"type": "object"}}
                ]
            }
        else:
            assert method == "tools/call"
            self.calls.append((message, request.headers.get("mcp-session-id")))
            if self.expire:
                self.expire = False
                return httpx.Response(404)
            self.entered.set()
            await self.release.wait()
            result = {"content": [{"type": "text", "text": self.label + " remote effect"}]}
        encoded = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}).encode()
        stream = Body(b"event: message\ndata: " + encoded + b"\n\n")
        self.bodies.append(stream)
        return httpx.Response(
            200,
            stream=stream,
            headers={
                "content-type": "text/event-stream",
                "mcp-session-id": str(self.handshakes),
            },
        )

    async def aclose(self):
        self.closed = True


def server(name="docs"):
    return MCPServerSettings(name, "http", url="https://fixture.invalid", environment_id="remote")


async def runtime_for(tmp_path, context, *, mode="direct", catalog=None):
    return await LangGraphRuntime.acreate(
        settings=CorkiSettings(
            working_directory=tmp_path,
            skills_enabled=False,
            api_mode="responses",
            tool_search_mode=mode if mode in ("native", "compatible") else "disabled",
            tool_mode="code_mode" if mode == "code_mode" else "direct",
            mcp_servers=(server(),),
        ),
        database_path=tmp_path / "history.db",
        home_path=tmp_path / "home",
        registry=ToolRegistry(),
        model=Model(mode),
        mcp_requirements={},
        mcp_catalog=catalog,
        mcp_runtime_context=context,
    )


@pytest.mark.parametrize("mode", ["direct", "native", "compatible", "code_mode"])
@pytest.mark.parametrize("expire", [False, True])
def test_bound_carrier_search_call_stream_recovery_and_host_ownership(tmp_path, mode, expire):
    async def scenario():
        carrier = Carrier(expire=expire)
        binding = MCPHTTPEnvironment("remote", carrier)
        runtime = await runtime_for(tmp_path, MCPRuntimeContext((binding,)), mode=mode)
        try:
            events = [event async for event in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted)
            assert runtime._mcp_manager.warnings == ()
            results = [
                item
                for item in runtime._model.requests[-1].items
                if isinstance(item, ToolResultItem)
            ]
            assert "first remote effect" in repr(results)
            assert len(carrier.calls) == 1 + expire
            assert carrier.handshakes == 1 + expire
            assert [session for _, session in carrier.calls] == (["1", "2"] if expire else ["1"])
        finally:
            await runtime.aclose()
        assert carrier.bodies and all(body.closed for body in carrier.bodies)
        assert any(request.method == "DELETE" for request in carrier.requests)
        assert not carrier.closed
        await carrier.aclose()

    asyncio.run(scenario())


def test_same_id_new_binding_switches_carrier_but_admitted_call_keeps_old_lease(tmp_path):
    async def scenario():
        first, second = Carrier(), Carrier("second")
        binding = MCPHTTPEnvironment("remote", first)
        runtime = await runtime_for(tmp_path, MCPRuntimeContext((binding,)))
        await runtime._ensure_ready()
        await runtime._mcp_manager.refresh_if_dirty()
        manager = runtime._mcp_manager
        old = manager._clients_by_name["docs"]
        try:
            runtime.request_mcp_runtime_context(MCPRuntimeContext((binding,)))
            await runtime._refresh_tools()
            assert manager._clients_by_name["docs"].client is old.client
            first.release.clear()
            call = asyncio.create_task(manager.call_tool("docs", "write", {"value": 1}))
            await asyncio.wait_for(first.entered.wait(), 2)
            runtime.request_mcp_runtime_context(
                MCPRuntimeContext((MCPHTTPEnvironment("remote", second),))
            )
            await runtime._refresh_tools()
            assert manager._clients_by_name["docs"].client is not old.client
            assert not old.client.is_closed
            result = await manager.call_tool("docs", "write", {"value": 2})
            assert "second remote effect" in repr(result)
            first.release.set()
            assert "first remote effect" in repr(await call)
        finally:
            first.release.set()
            await runtime.aclose()
        assert old.client.is_closed and not first.closed and not second.closed
        await first.aclose()
        await second.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "kind", ["config", "plugin", "selected_plugin", "compatibility", "extension"]
)
def test_owner_policy_rejects_all_sources_before_http_startup(tmp_path, kind):
    async def scenario():
        carrier = Carrier()
        source = MCPCatalogSource(kind, None if kind == "config" else "package")
        catalog = MCPCatalog((MCPRegistration(server(), source),))
        binding = MCPHTTPEnvironment("remote", carrier, requirements=MCPRequirements(servers=()))
        runtime = await runtime_for(tmp_path, MCPRuntimeContext((binding,)), catalog=catalog)
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.refresh_if_dirty()
            assert carrier.requests == []
            assert not runtime.mcp_catalog.servers[0].settings.enabled
            assert "environment requirements" in repr(runtime._mcp_manager.warnings)
        finally:
            await runtime.aclose()
        assert not carrier.closed

    asyncio.run(scenario())


def test_binding_mismatch_rejected_before_http_allocation(monkeypatch):
    binding = MCPHTTPEnvironment("different", Carrier())

    def forbidden(*args, **kwargs):
        raise AssertionError("must reject before allocation")

    monkeypatch.setattr("corki.mcp.client.MCPHttpSession", forbidden)
    with pytest.raises(MCPProtocolError, match="does not match"):
        HttpMCPClient(server(), environment=binding)


def test_invalid_context_rejected_before_runtime_allocates_resources(tmp_path):
    with pytest.raises(ValueError, match="host-owned"):
        asyncio.run(runtime_for(tmp_path, {"remote": Carrier()}))
    assert not (tmp_path / "history.db").exists()


@pytest.mark.parametrize("change", ["detach", "deny", "replace_same_carrier"])
def test_changed_host_binding_controls_next_admission(tmp_path, change):
    async def scenario():
        carrier = Carrier()
        binding = MCPHTTPEnvironment("remote", carrier)
        runtime = await runtime_for(tmp_path, MCPRuntimeContext((binding,)))
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.refresh_if_dirty()
            old = runtime._mcp_manager._clients_by_name["docs"].client
            replacement = (
                ()
                if change == "detach"
                else (
                    MCPHTTPEnvironment(
                        "remote",
                        carrier,
                        requirements=MCPRequirements(servers=())
                        if change == "deny"
                        else MCPRequirements(),
                    ),
                )
            )
            runtime.request_mcp_runtime_context(MCPRuntimeContext(replacement))
            if change == "replace_same_carrier":
                assert "remote effect" in repr(
                    await runtime._mcp_manager.call_tool("docs", "write", {})
                )
                assert carrier.handshakes == 2
                assert runtime._mcp_manager._clients_by_name["docs"].client is not old
            else:
                with pytest.raises((KeyError, MCPProtocolError)):
                    await runtime._mcp_manager.call_tool("docs", "write", {})
                assert carrier.calls == []
                assert "docs" not in runtime._mcp_manager._clients_by_name
            # Restoring the exact old handle after removal cannot reuse its retired session.
            runtime.request_mcp_runtime_context(MCPRuntimeContext((binding,)))
            await runtime._refresh_tools()
            # A cached definition can now satisfy Step capture before the new
            # transport is ready. Check connection identity at live admission.
            await runtime._mcp_manager.prepare_server("docs")
            assert runtime._mcp_manager._clients_by_name["docs"].client is not old
        finally:
            await runtime.aclose()
        assert not carrier.closed
        await carrier.aclose()

    asyncio.run(scenario())


def test_shared_environment_survives_one_server_removal_and_other_runtime_shutdown(tmp_path):
    async def scenario():
        carrier = Carrier()
        binding = MCPHTTPEnvironment("remote", carrier)
        context = MCPRuntimeContext((binding,))
        runtime = await runtime_for(tmp_path, context)
        manager = MCPManager(
            (server("one"), server("two")), ToolRegistry(), runtime_context=context
        )
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.refresh_if_dirty()
            await manager.start()
            await runtime.aclose()
            manager.request_reconcile((server("two"),))
            result = await manager.call_tool("two", "write", {})
            assert "remote effect" in repr(result)
            assert not carrier.closed
        finally:
            await manager.aclose()
            await runtime.aclose()
        assert not carrier.closed and all(body.closed for body in carrier.bodies)
        await carrier.aclose()

    asyncio.run(scenario())


def test_owner_denied_config_winner_does_not_fall_back_to_allowed_plugin(tmp_path):
    async def scenario():
        carrier = Carrier()
        policy = MCPRequirements.from_mapping(
            {"mcp_servers": {"other": {"identity": {"url": "https://other.invalid"}}}}
        )
        context = MCPRuntimeContext((MCPHTTPEnvironment("remote", carrier, policy),))
        catalog = MCPCatalog(
            (
                MCPRegistration(server(), MCPCatalogSource("plugin", "package")),
                MCPRegistration(server()),
            )
        )
        runtime = await runtime_for(tmp_path, context, catalog=catalog)
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.refresh_if_dirty()
            assert runtime.mcp_catalog.servers[0].source.kind == "config"
            assert not runtime.mcp_catalog.servers[0].settings.enabled
            assert carrier.requests == []
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["plugin", "selected_plugin"])
def test_owner_plugin_policy_uses_package_identity_not_route_prefix(tmp_path, kind):
    async def scenario():
        carrier = Carrier()
        policy = MCPRequirements.from_mapping(
            {
                "plugins": {
                    "package": {
                        "mcp_servers": {"docs": {"identity": {"url": "https://fixture.invalid"}}}
                    }
                }
            }
        )
        context = MCPRuntimeContext((MCPHTTPEnvironment("remote", carrier, policy),))
        catalog = MCPCatalog((MCPRegistration(server(), MCPCatalogSource(kind, "package")),))
        runtime = await runtime_for(tmp_path, context, catalog=catalog)
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.refresh_if_dirty()
            assert runtime.mcp_catalog.servers[0].settings.enabled
            assert "remote effect" in repr(
                await runtime._mcp_manager.call_tool("docs", "write", {})
            )
            runtime.request_mcp_catalog(
                MCPCatalog((MCPRegistration(server(), MCPCatalogSource(kind, "different")),))
            )
            await runtime._refresh_tools()
            assert not runtime.mcp_catalog.servers[0].settings.enabled
            assert carrier.handshakes == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_bound_http_environment_does_not_enable_local_stdio_or_transport_override():
    binding = MCPHTTPEnvironment("remote", Carrier())
    context = MCPRuntimeContext((binding,))
    with pytest.raises(MCPProtocolError, match="no stdio capability"):
        context.resolve(
            MCPServerSettings("docs", "stdio", command="never", environment_id="remote")
        )
    with pytest.raises(ValueError, match="transport selection"):
        HttpMCPClient(server(), environment=binding, transport=Carrier())


def test_context_captures_list_without_copying_transport_or_binding_identity():
    binding = MCPHTTPEnvironment("remote", Carrier())
    original = [binding]
    context = MCPRuntimeContext(original)
    original.clear()
    assert context.resolve(server()) is binding
    assert context.resolve(replace(server(), environment_id="local")) is None
    with pytest.raises(ValueError, match="unique"):
        MCPRuntimeContext((binding, binding))


def test_cancelled_bound_call_releases_session_but_not_shared_environment(tmp_path):
    async def scenario():
        carrier = Carrier()
        runtime = await runtime_for(
            tmp_path, MCPRuntimeContext((MCPHTTPEnvironment("remote", carrier),))
        )
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.refresh_if_dirty()
            carrier.release.clear()
            call = asyncio.create_task(runtime._mcp_manager.call_tool("docs", "write", {}))
            await asyncio.wait_for(carrier.entered.wait(), 2)
            call.cancel()
            with pytest.raises(asyncio.CancelledError):
                await call
            assert len(carrier.calls) == 1
        finally:
            carrier.release.set()
            await runtime.aclose()
        assert not carrier.closed
        assert all(body.closed for body in carrier.bodies)

    asyncio.run(scenario())


def test_context_replaced_during_initialization_is_not_lost(tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        class SlowCarrier(Carrier):
            async def handle_async_request(self, request):
                if (
                    request.method == "POST"
                    and json.loads(request.content).get("method") == "initialize"
                ):
                    entered.set()
                    await release.wait()
                return await super().handle_async_request(request)

        first, second = SlowCarrier(), Carrier("second")
        runtime = await runtime_for(
            tmp_path, MCPRuntimeContext((MCPHTTPEnvironment("remote", first),))
        )
        startup = asyncio.create_task(runtime._ensure_ready())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            runtime.request_mcp_runtime_context(
                MCPRuntimeContext((MCPHTTPEnvironment("remote", second),))
            )
            release.set()
            await startup
            assert "second remote effect" in repr(
                await runtime._mcp_manager.call_tool("docs", "write", {})
            )
            assert first.calls == [] and second.handshakes == 1
            assert not runtime._mcp_manager.refresh_pending
        finally:
            release.set()
            await startup
            await runtime.aclose()
        assert not first.closed and not second.closed

    asyncio.run(scenario())


def test_failed_publication_retains_old_binding_and_retries_desired_context(tmp_path, monkeypatch):
    async def scenario():
        first, second = Carrier(), Carrier("second")
        runtime = await runtime_for(
            tmp_path, MCPRuntimeContext((MCPHTTPEnvironment("remote", first),))
        )
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.refresh_if_dirty()
            manager = runtime._mcp_manager
            old = manager._clients_by_name["docs"]
            publish = runtime._registry.replace_owned
            failures = []

            def failed(*args, **kwargs):
                failures.append(second.handshakes)
                raise RuntimeError("publication failure")

            runtime.request_mcp_runtime_context(
                MCPRuntimeContext((MCPHTTPEnvironment("remote", second),))
            )
            with monkeypatch.context() as patch:
                patch.setattr(runtime._registry, "replace_owned", failed)
                with pytest.raises(RuntimeError, match="publication failure"):
                    await runtime._refresh_tools()
            assert runtime._registry.replace_owned == publish
            assert manager.refresh_pending and manager._clients_by_name["docs"] is old
            assert not old.client.is_closed and not second.closed
            assert all(body.closed for body in second.bodies)
            await runtime._refresh_tools()
            assert "second remote effect" in repr(await manager.call_tool("docs", "write", {}))
            # The foreground claimant and the one queued prewarm notification
            # can each encounter the injected fault. Every failed connection is
            # retired; exactly one subsequent handshake succeeds, with no spin.
            assert 1 <= len(failures) <= 2
            assert second.handshakes == len(failures) + 1 and first.calls == []
            assert len(second.calls) == 1
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
