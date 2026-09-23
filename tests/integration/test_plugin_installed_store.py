"""Configured native installation roots must reach Runtime discovery and MCP calls."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.models import ModelCompleted
from corki.plugins.manifest_path import AGENT_SCHEMA
from corki.plugins.store import PluginId
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["compatible", "native", "code_mode"])
@pytest.mark.parametrize(
    "case",
    [
        "configured",
        "local",
        "semver",
        "unconfigured",
        "disabled",
        "server_disabled",
        "reload_remove",
        "reload_disabled",
        "reload_invalid",
        "reload_version",
        "cold_version",
        "agent_data",
        "agent_data_blocked",
    ],
)
def test_native_installed_plugin_selection_reaches_runtime(tmp_path, monkeypatch, mode, case):
    async def scenario():
        config = tmp_path / "config.toml"
        trigger_config = (
            '[mcp.servers.reload_trigger]\ntransport="http"\n'
            'url="https://reload-trigger.invalid/mcp"\nenabled=false\n'
        )
        home = tmp_path / "home"
        versions = (
            ("9.0.0", "10.0.0")
            if case == "semver"
            else ("local", "99.0.0")
            if case == "local"
            else ("9.0.0",)
            if case in {"reload_version", "cold_version"}
            else ("local",)
        )
        for version in versions:
            root = home / "plugins/cache/lab/fixture" / version
            (root / ".codex-plugin").mkdir(parents=True)
            (root / ".codex-plugin/plugin.json").write_text(
                json.dumps({"name": "fixture", "version": "999.0.0"})
            )
            (root / ".mcp.json").write_text(
                json.dumps({"mcpServers": {"docs": {"url": f"https://fixture.invalid/{version}"}}})
            )
        data = PluginId.parse("fixture@lab").data_root(home, agent_plugin=True)
        if case.startswith("agent_data"):
            (root / "plugin.json").write_text(
                json.dumps({"$schema": AGENT_SCHEMA, "name": "fixture"})
            )
            (root / "mcp.json").write_text(
                json.dumps(
                    {
                        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
                        "mcpServers": {
                            "local": {"type": "stdio", "command": "echo"},
                            "docs": {
                                "type": "streamable-http",
                                "url": "https://fixture.invalid/local",
                            },
                        },
                    }
                )
            )
            if case == "agent_data_blocked":
                data.parent.mkdir(parents=True)
                data.write_text("owned file")
        config.write_text(
            trigger_config
            + (
                ""
                if case == "unconfigured"
                else '[plugins."fixture@lab"]\nenabled='
                + ("false" if case == "disabled" else "true")
                + "\n"
                + (
                    '[plugins."fixture@lab".mcp_servers.docs]\nenabled=false\n'
                    if case == "server_disabled"
                    else ""
                )
            )
        )
        clients, calls, requests, transports = [], [], [], []

        def factory(settings, **client_options):
            assert settings.name != "reload_trigger", "disabled persistence target must not connect"
            transports.append(settings.transport)
            local = settings.transport == "stdio"
            if local:
                # The discovery worker must finish directory creation before startup.
                assert data.is_dir()
                assert dict(settings.env)["PLUGIN_DATA"] == str(data.resolve())

            async def respond(request):
                packet = json.loads(request.content)
                if packet["method"] == "notifications/initialized":
                    return httpx.Response(202)
                if packet["method"] == "initialize":
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "fixture", "version": "1"},
                    }
                elif packet["method"] == "tools/list":
                    result = {
                        "tools": [
                            {
                                "name": name,
                                "description": "POLICY_FIXTURE " + name,
                                "inputSchema": {"type": "object"},
                                "annotations": {"readOnlyHint": True},
                            }
                            for name in (() if local else ("read", "write"))
                        ]
                    }
                else:
                    assert packet["method"] == "tools/call"
                    calls.append((packet["params"]["name"], settings.url))
                    result = {"content": [{"type": "text", "text": "fixture result"}]}
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
                )

            # Scripted transport exercises Runtime startup ownership, not a real child process.
            client = HttpMCPClient(
                MCPServerSettings(settings.name, "http", url="https://stdio-fixture.invalid")
                if local
                else settings,
                transport=httpx.MockTransport(respond),
                **client_options,
            )
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        monkeypatch.setattr("corki.mcp.manager.HttpMCPClient", factory)

        class Model:
            steps = 0

            async def stream(self, request):
                requests.append(request)
                self.steps += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                call = None
                if self.steps == 1:
                    if mode == "code_mode":
                        call = ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments=(
                                "const t=ALL_TOOLS.find(t=>"
                                "t.description.includes('POLICY_FIXTURE write'));"
                                "if(t)text(await tools[t.name]({}));"
                            ),
                        )
                    else:
                        call = ToolCall(
                            new_tool_call_id(), "tool_search", {"query": "POLICY_FIXTURE"}
                        )
                elif self.steps == 2 and mode != "code_mode":
                    results = [i for i in request.items if isinstance(i, ToolResultItem)]
                    found = next(
                        (
                            t
                            for t in results[-1].discovered_tools
                            if "POLICY_FIXTURE write" in t.description
                        ),
                        None,
                    )
                    if found is not None:
                        call = ToolCall(new_tool_call_id(), found.name, {})
                if call is not None:
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        settings = replace(
            CorkiSettings.for_directory(tmp_path, config_file=config),
            execution_permissions=None,
            api_mode="responses",
            model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
            tool_search_mode="disabled" if mode == "code_mode" else mode,
            tool_mode="code_mode_only" if mode == "code_mode" else "direct",
        )
        model = Model()
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            model=model,
            registry=ToolRegistry(),
            database_path=tmp_path / "runtime.db",
            home_path=home,
        )
        try:
            events = [event async for event in runtime.stream("Find and use POLICY_FIXTURE write")]
            assert isinstance(events[-1], TurnCompleted)
            lifecycle = case.startswith("reload_") or case == "cold_version"
            if lifecycle:
                initial_version = "9.0.0" if case.endswith("version") else "local"
                assert calls == [("write", f"https://fixture.invalid/{initial_version}")]
                calls.clear()
                model.steps = 0
                if case.endswith("version"):
                    root = home / "plugins/cache/lab/fixture/10.0.0"
                    (root / ".codex-plugin").mkdir(parents=True)
                    (root / ".codex-plugin/plugin.json").write_text('{"name":"fixture"}')
                    (root / ".mcp.json").write_text(
                        json.dumps(
                            {"mcpServers": {"docs": {"url": "https://fixture.invalid/10.0.0"}}}
                        )
                    )
                else:
                    config.write_text(
                        trigger_config
                        + {
                            "reload_remove": "",
                            "reload_disabled": '[plugins."fixture@lab"]\nenabled=false\n',
                            "reload_invalid": (
                                '[plugins."fixture@lab"]\nenabled=true\n'
                                '[plugins."unrelated@lab"]\nenabled="bad"\n'
                            ),
                        }[case]
                    )
                if case == "cold_version":
                    thread = runtime.thread_id
                    await runtime.aclose()
                    assert all(client.is_closed for client in clients)
                    runtime = await LangGraphRuntime.acreate(
                        settings=settings,
                        model=model,
                        registry=ToolRegistry(),
                        database_path=tmp_path / "runtime.db",
                        home_path=home,
                        thread_id=thread,
                    )
                else:
                    persistence = runtime._mcp_manager._approval_persistence
                    await persistence.persist(
                        ("reload_trigger", "fixture", "Fixture", "trigger"),
                        persistence.configuration,
                        None,
                    )
                    assert "apps" not in persistence.document
                    assert (
                        persistence.document["mcp"]["servers"]["reload_trigger"]["tools"][
                            "trigger"
                        ]["approval_mode"]
                        == "approve"
                    )
                events = [event async for event in runtime.stream("Find POLICY_FIXTURE again")]
        finally:
            await runtime.aclose()
        assert isinstance(events[-1], TurnCompleted)
        assert all(client.is_closed for client in clients)
        allowed = case in {
            "configured",
            "local",
            "semver",
            "reload_version",
            "cold_version",
            "agent_data",
            "agent_data_blocked",
        }
        version = "10.0.0" if case in {"semver", "reload_version", "cold_version"} else "local"
        assert calls == ([("write", f"https://fixture.invalid/{version}")] if allowed else []), (
            runtime._plugin_manager.warnings,
            transports,
            runtime._mcp_manager.catalog.servers,
        )
        if allowed:
            entry = runtime._mcp_manager.catalog.servers[0]
            assert entry.source.identity == "fixture@lab"
        elif not lifecycle:
            assert not clients
        if not allowed and case != "server_disabled":
            assert all(
                entry.settings.name == "reload_trigger" and not entry.settings.enabled
                for entry in runtime._mcp_manager.catalog.servers
            )
        if case == "server_disabled":
            assert all(not entry.settings.enabled for entry in runtime._mcp_manager.catalog.servers)
        if allowed:
            # The final model sampling must actually observe the tool result.
            assert any(
                isinstance(item, ToolResultItem) and "fixture result" in item.content
                for item in requests[-1].items
            )
        if case == "agent_data":
            assert sorted(transports) == ["http", "stdio"]
            assert data.is_dir()
        elif case == "agent_data_blocked":
            assert transports == ["http"]
            assert data.read_text() == "owned file"
            assert any("disabling stdio" in warning for warning in runtime._plugin_manager.warnings)

    asyncio.run(scenario())
