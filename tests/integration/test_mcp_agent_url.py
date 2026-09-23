"""Pinned URL parser cases through actual plugin discovery, recall and HTTP calls."""

import asyncio
import json

import httpx
import pytest
from test_mcp_agent_plugin import package, runtime_for
from test_mcp_bound_http import Body, Carrier

from corki.protocol.events import TurnCompleted
from corki.protocol.items import ToolResultItem

# Observed with url=2.5.8 and every transitive version/checksum from Codex's lock.
CASES = [
    ("http://127.1/mcp", "http://127.0.0.1/mcp"),
    ("http://2130706433/mcp", "http://127.0.0.1/mcp"),
    ("http://0177.0.0.1/mcp", "http://127.0.0.1/mcp"),
    ("http://0x7f000001/mcp", "http://127.0.0.1/mcp"),
    ("http://127.0.0.1./mcp", "http://127.0.0.1/mcp"),
    ("http:localhost/mcp", "http://localhost/mcp"),
    ("https:example.test/mcp", "https://example.test/mcp"),
    (" HTTPS://example.test/mcp ", "https://example.test/mcp"),
    ("https://example.test/%2e%2e/mcp", "https://example.test/mcp"),
    ("http://%6cocalhost/mcp", "http://localhost/mcp"),
    ("https://example.test\\mcp", "https://example.test/mcp"),
    ("https://:@example.test/mcp", "https://example.test/mcp"),
]


@pytest.mark.parametrize("raw,canonical", CASES)
@pytest.mark.parametrize("mode", ["direct", "native", "compatible", "code_mode"])
def test_agent_url_admission_and_wire_destination_agree(
    tmp_path, monkeypatch, raw, canonical, mode
):
    async def scenario():
        root = package(tmp_path, overlay=False)
        source = root / "mcp.json"
        value = json.loads(source.read_text())
        value["mcpServers"]["docs"]["url"] = raw
        source.write_text(json.dumps(value))
        carrier = Carrier()
        runtime = await runtime_for(tmp_path, monkeypatch, carrier, mode)
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.refresh_if_dirty()
            assert runtime._plugin_manager.warnings == ()
            assert runtime._mcp_manager.warnings == ()
            # Raw spelling remains policy identity; only transport gets canonicalized.
            assert runtime.mcp_catalog.servers[0].settings.url == raw
            events = [event async for event in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(carrier.calls) == 1
            results = [
                i for i in runtime._model.requests[-1].items if isinstance(i, ToolResultItem)
            ]
            assert "first remote effect" in repr(results)
            if mode in ("native", "compatible"):
                assert any(i.tool_name == "tool_search" for i in results)
        finally:
            await runtime.aclose()
        assert carrier.requests and all(str(r.url) == canonical for r in carrier.requests)
        assert carrier.closed and all(b.closed for b in carrier.bodies)

    asyncio.run(scenario())


def test_agent_invalid_scoped_ipv6_never_reaches_the_carrier(tmp_path, monkeypatch):
    async def scenario():
        root = package(tmp_path, overlay=False)
        source = root / "mcp.json"
        value = json.loads(source.read_text())
        value["mcpServers"]["docs"]["url"] = "https://[::1%25eth0]/mcp"
        source.write_text(json.dumps(value))
        carrier = Carrier()
        runtime = await runtime_for(tmp_path, monkeypatch, carrier)
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.refresh_if_dirty()
            assert len(runtime._plugin_manager.plugins) == 1
            assert runtime._plugin_manager.warnings
            assert carrier.requests == []
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "location", ["/%2e/final", "/a/%2e%2e/final", "https://%66ixture.invalid/final", "\\final"]
)
@pytest.mark.parametrize("mode", ["direct", "native", "compatible", "code_mode"])
def test_agent_unauthenticated_redirect_uses_the_same_url_parser(
    tmp_path, monkeypatch, location, mode
):
    class RedirectCarrier(Carrier):
        async def handle_async_request(self, request):
            if request.url.path != "/final":
                self.requests.append(request)
                body = Body(b"not drained")
                self.bodies.append(body)
                return httpx.Response(307, headers={"location": location}, stream=body)
            return await super().handle_async_request(request)

    async def scenario():
        root = package(tmp_path, overlay=False)
        source = root / "mcp.json"
        value = json.loads(source.read_text())
        value["mcpServers"]["docs"]["headers"] = {}
        source.write_text(json.dumps(value))
        carrier = RedirectCarrier()
        runtime = await runtime_for(tmp_path, monkeypatch, carrier, mode)
        try:
            events = [e async for e in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted)
            assert runtime._mcp_manager.warnings == ()
            assert carrier.handshakes == 1 and len(carrier.calls) == 1
        finally:
            await runtime.aclose()
        assert {str(r.url) for r in carrier.requests} == {
            "https://fixture.invalid/start",
            "https://fixture.invalid/final",
        }
        assert carrier.closed and all(b.closed for b in carrier.bodies)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["fuel", "memory", "module"])
def test_agent_url_engine_failure_never_falls_back_to_unvalidated_http(
    tmp_path, monkeypatch, failure
):
    async def scenario():
        package(tmp_path, overlay=False)
        if failure == "fuel":
            monkeypatch.setattr("corki.config.mcp_url._FUEL", 1)
        elif failure == "memory":
            monkeypatch.setattr("corki.config.mcp_url._MEMORY_BYTES", 1024)
        else:

            def unavailable():
                raise OSError("fixture missing parser")

            monkeypatch.setattr("corki.config.mcp_url._engine", unavailable)
        carrier = Carrier()
        runtime = await runtime_for(tmp_path, monkeypatch, carrier)
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.refresh_if_dirty()
            assert len(runtime._plugin_manager.plugins) == 1
            assert any(
                "URL engine unavailable or resource limit" in w
                for w in runtime._plugin_manager.warnings
            )
            assert carrier.requests == []
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
