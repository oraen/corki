"""Root Agent Plugin identity must reach actual Runtime admission and HTTP policy."""

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest
from test_mcp_bound_http import Carrier
from test_mcp_redirect_policy import RedirectingCarrier
from test_mcp_tool_approval import Model

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.mcp.http_redirect import MCPHttpSession
from corki.mcp.input_schema import json_bytes
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ToolResultItem
from corki.protocol.tools import ToolExposure
from corki.tools import ToolRegistry

SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"


@pytest.mark.parametrize("size", [8000, 8001])
def test_real_plugin_refresh_budgets_ordinary_definitions(tmp_path, monkeypatch, size):
    async def scenario():
        package(tmp_path, overlay=False)
        clients, calls = [], []
        definition = {
            "name": "write",
            "description": "needle",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "padding": {"type": "string", "enum": [""]},
                },
            },
        }

        def respond(request):
            assert str(request.url) == "https://fixture.invalid/start"
            packet = json.loads(request.content)
            if "id" not in packet:
                return httpx.Response(202)
            if packet["method"] == "initialize":
                result = {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "serverInfo": {"name": "fixture", "version": "1"},
                }
            elif packet["method"] == "tools/list":
                result = {"tools": [definition]}
            else:
                assert packet["method"] == "tools/call"
                calls.append(packet)
                result = {"content": [{"type": "text", "text": "remote effect"}]}
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
            )

        def session(**kwargs):
            kwargs["transport"] = httpx.MockTransport(respond)
            client = MCPHttpSession(**kwargs)
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.client.MCPHttpSession", session)
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                plugin_dirs=(tmp_path / ".corki/plugins",),
                skills_enabled=False,
                api_mode="responses",
                tool_search_mode="disabled",
            ),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
            registry=ToolRegistry(),
            model=Model("direct"),
            mcp_requirements={},
        )
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.refresh_if_dirty()
            name = "mcp__docs::write"
            spec = runtime._registry.spec(name)
            assert spec is not None, runtime._mcp_manager.warnings
            base = max(
                json_bytes(spec.as_response_tool()), json_bytes(spec.as_chat_completion_tool())
            )
            definition["inputSchema"]["properties"]["padding"]["enum"] = ["x" * (size - base)]
            runtime.request_mcp_refresh()
            await runtime._mcp_manager.refresh_if_dirty()
            spec = runtime._registry.spec(name)
            assert (
                max(json_bytes(spec.as_response_tool()), json_bytes(spec.as_chat_completion_tool()))
                == size
            )
            assert spec.exposure == (ToolExposure.DIRECT if size == 8000 else ToolExposure.HIDDEN)
            if size == 8000:
                events = [event async for event in runtime.stream("needle")]
                assert isinstance(events[-1], TurnCompleted), events[-1]
                assert len(calls) == 1
            else:
                assert runtime._registry.get(name).mcp_omit_tools_from == (
                    "direct",
                    "deferred",
                    "code_mode",
                )
                assert not calls
        finally:
            await runtime.aclose()
        assert len(clients) == 2 and all(client.is_closed for client in clients)

    asyncio.run(scenario())


def package(tmp_path, *, overlay=True, schema=SCHEMA):
    root = tmp_path / ".corki/plugins/sample"
    root.mkdir(parents=True)
    (root / "plugin.json").write_text(json.dumps({"$schema": schema, "name": "sample"}))
    (root / "mcp.json").write_text(
        json.dumps(
            {
                "$schema": MCP_SCHEMA,
                "mcpServers": {
                    "docs": {
                        "type": "streamable-http",
                        "url": "https://fixture.invalid/start",
                        "headers": {"x-api-key": "fixture"},
                    },
                },
            }
        )
    )
    if overlay:
        legacy = root / ".codex-plugin/plugin.json"
        legacy.parent.mkdir()
        legacy.write_text(
            json.dumps(
                {
                    "name": "decoy",
                    "mcpServers": {
                        "docs": {
                            "url": "https://fixture.invalid/start",
                            "http_headers": {"x-api-key": "fixture"},
                        },
                    },
                }
            )
        )
    return root


async def runtime_for(tmp_path, monkeypatch, carrier, mode="direct"):
    def session(**kwargs):
        kwargs["transport"] = carrier
        return MCPHttpSession(**kwargs)

    monkeypatch.setattr("corki.mcp.client.MCPHttpSession", session)
    return await LangGraphRuntime.acreate(
        settings=CorkiSettings(
            working_directory=tmp_path,
            plugin_dirs=(tmp_path / ".corki/plugins",),
            skills_enabled=False,
            api_mode="responses",
            tool_search_mode=mode if mode in ("native", "compatible") else "disabled",
            tool_mode="code_mode" if mode == "code_mode" else "direct",
        ),
        database_path=tmp_path / "history.db",
        home_path=tmp_path / "home",
        registry=ToolRegistry(),
        model=Model(mode),
        mcp_requirements={},
    )


@pytest.mark.parametrize("overlay", [False, True])
@pytest.mark.parametrize("mode", ["direct", "native", "compatible", "code_mode"])
def test_agent_plugin_root_discovers_and_calls_with_trusted_format(
    tmp_path, monkeypatch, mode, overlay
):
    async def scenario():
        package(tmp_path, overlay=overlay)
        carrier = Carrier()
        runtime = await runtime_for(tmp_path, monkeypatch, carrier, mode)
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.refresh_if_dirty()
            assert runtime._mcp_manager.warnings == ()
            assert [p.manifest.name for p in runtime._plugin_manager.plugins] == ["sample"]
            assert runtime.mcp_catalog.servers[0].source.agent_plugin
            events = [e async for e in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(carrier.calls) == 1
            results = [
                i for i in runtime._model.requests[-1].items if isinstance(i, ToolResultItem)
            ]
            assert "first remote effect" in repr(results)
            assert all(r.headers.get("x-api-key") == "fixture" for r in carrier.requests)
        finally:
            await runtime.aclose()
        assert carrier.closed and all(b.closed for b in carrier.bodies)

    asyncio.run(scenario())


def test_agent_plugin_overlay_cannot_downgrade_authenticated_redirect(tmp_path, monkeypatch):
    async def scenario():
        package(tmp_path)
        carrier = RedirectingCarrier()
        runtime = await runtime_for(tmp_path, monkeypatch, carrier)
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.refresh_if_dirty()
            assert carrier.requests  # Actual attempted initialize, not silent feature disable.
            assert carrier.handshakes == 0
            assert all(r.url.path != "/final" for r in carrier.requests)
            assert runtime._mcp_manager.warnings
            assert "docs" not in runtime._mcp_manager._clients_by_name
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


def test_agent_client_owned_headers_do_not_become_plugin_credentials(tmp_path, monkeypatch):
    async def scenario():
        root = package(tmp_path, overlay=False)
        source = root / "mcp.json"
        document = json.loads(source.read_text())
        document["mcpServers"]["docs"]["headers"] = {
            "Authorization": "ignored-fixture",
            "Host": "ignored.test",
            "User-Agent": "ignored-fixture",
        }
        source.write_text(json.dumps(document))
        carrier = RedirectingCarrier()
        runtime = await runtime_for(tmp_path, monkeypatch, carrier)
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.refresh_if_dirty()
            assert runtime.mcp_catalog.servers[0].source.agent_plugin
            assert runtime._mcp_manager.warnings == ()
            assert carrier.handshakes == 1
            assert any(r.url.path == "/final" for r in carrier.requests)
            assert all(
                "authorization" not in r.headers and r.headers.get("host") != "ignored.test"
                for r in carrier.requests
            )
        finally:
            await runtime.aclose()
        assert carrier.closed and all(b.closed for b in carrier.bodies)


def test_agent_unicode_header_survives_actual_transport(tmp_path, monkeypatch):
    async def scenario():
        root = package(tmp_path, overlay=False)
        source = root / "mcp.json"
        document = json.loads(source.read_text())
        document["mcpServers"]["docs"]["headers"] = {"X-Plugin": "café"}
        source.write_text(json.dumps(document))
        carrier = Carrier()
        runtime = await runtime_for(tmp_path, monkeypatch, carrier)
        try:
            events = [e async for e in runtime.stream("needle")]
            assert isinstance(events[-1], TurnCompleted)
            assert len(carrier.calls) == 1
            assert all((b"x-plugin", "café".encode()) in r.headers.raw for r in carrier.requests)
        finally:
            await runtime.aclose()
        assert carrier.closed and all(b.closed for b in carrier.bodies)


@pytest.mark.parametrize("kind", ["unsupported", "symlink", "directory"])
def test_invalid_agent_root_never_falls_back_to_legacy_overlay(tmp_path, monkeypatch, kind):
    async def scenario():
        root = package(
            tmp_path, schema=SCHEMA.replace("1.0.0", "9.0.0") if kind == "unsupported" else SCHEMA
        )
        if kind != "unsupported":
            original = root / "original.json"
            (root / "plugin.json").rename(original)
            if kind == "symlink":
                (root / "plugin.json").symlink_to(original)
            else:
                (root / "plugin.json").mkdir()
        carrier = Carrier()
        runtime = await runtime_for(tmp_path, monkeypatch, carrier)
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.refresh_if_dirty()
            assert runtime._plugin_manager.plugins == ()
            assert carrier.requests == []
        finally:
            await runtime.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "native", "compatible", "code_mode"])
@pytest.mark.parametrize("inline", [False, True])
def test_agent_stdio_source_environment_reaches_actual_process(tmp_path, monkeypatch, mode, inline):
    async def scenario():
        root = package(tmp_path, overlay=False)
        script = Path(__file__).resolve().parents[1] / "fixtures/agent_plugin_mcp_server.py"
        report = tmp_path / "process-report.json"
        token = "CORKI_PLUGIN_FIXTURE_TOKEN"
        static = "CORKI_PLUGIN_FIXTURE_STATIC"
        monkeypatch.setenv(token, "inherited-fixture")
        monkeypatch.setenv(static, "must-not-replace-literal")
        (root / "mcp.json").write_text(
            json.dumps(
                {
                    "$schema": MCP_SCHEMA,
                    "mcpServers": {
                        "docs": {
                            "type": "stdio",
                            "command": Path(sys.executable).name,
                            "args": [str(script), str(report)],
                            "env": {
                                "PATH": str(Path(sys.executable).parent),
                                token: "${" + token + "}",
                                static: "literal-fixture",
                            },
                        },
                    },
                }
            )
        )
        servers = {
            "docs": {"command": "must-not-execute-overlay", "env_vars": [token, static]},
            "extra": {"command": "must-not-add-server"},
        }
        overlay = root / ".codex-plugin/plugin.json"
        overlay.parent.mkdir()
        overlay.write_text(
            json.dumps({"name": "decoy", **({"mcpServers": servers} if inline else {})})
        )
        if not inline:
            (root / ".mcp.json").write_text(json.dumps(servers))
        carrier = Carrier()
        runtime = await runtime_for(tmp_path, monkeypatch, carrier, mode)
        process = None
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.refresh_if_dirty()
            assert runtime._mcp_manager.warnings == ()
            assert set(runtime._mcp_manager._clients_by_name) == {"docs"}
            assert runtime.mcp_catalog.servers[0].source.agent_plugin
            process = runtime._mcp_manager._clients_by_name["docs"].client._process
            events = [e async for e in runtime.stream("needle plugin env")]
            assert isinstance(events[-1], TurnCompleted)
            assert json.loads(report.read_text()) == {
                "cwd": str(root.resolve()),
                "calls": 1,
                "env": {
                    "PLUGIN_ROOT": str(root.resolve()),
                    "PLUGIN_DATA": str(root.resolve() / ".plugin-data"),
                    token: "inherited-fixture",
                    static: "literal-fixture",
                },
            }
            results = [
                i for i in runtime._model.requests[-1].items if isinstance(i, ToolResultItem)
            ]
            assert "inherited-fixture" in repr(results)
            if mode in ("native", "compatible"):
                assert any(i.tool_name == "tool_search" for i in results)
            assert not (root / ".plugin-data").exists()
        finally:
            await runtime.aclose()
        assert process is not None and process.returncode is not None
        assert not carrier.requests

    asyncio.run(scenario())
