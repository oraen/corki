"""Configured native installation roots must reach Runtime discovery and MCP calls."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["compatible", "native", "code_mode"])
@pytest.mark.parametrize(
    "case", ["configured", "local", "semver", "unconfigured", "disabled", "server_disabled"]
)
def test_native_installed_plugin_selection_reaches_runtime(tmp_path, monkeypatch, mode, case):
    async def scenario():
        config = tmp_path / "config.toml"
        home = tmp_path / "home"
        versions = (
            ("9.0.0", "10.0.0")
            if case == "semver"
            else ("local", "99.0.0")
            if case == "local"
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
        config.write_text(
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
        clients, calls = [], []

        def factory(settings):
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
                            for name in ("read", "write")
                        ]
                    }
                else:
                    assert packet["method"] == "tools/call"
                    calls.append((packet["params"]["name"], settings.url))
                    result = {"content": [{"type": "text", "text": "fixture result"}]}
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(respond))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)

        class Model:
            steps = 0

            async def stream(self, request):
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
        runtime = LangGraphRuntime.create(
            settings=settings,
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "runtime.db",
            home_path=home,
        )
        try:
            events = [event async for event in runtime.stream("Find and use POLICY_FIXTURE write")]
        finally:
            await runtime.aclose()
        assert isinstance(events[-1], TurnCompleted)
        assert all(client.is_closed for client in clients)
        allowed = case in {"configured", "local", "semver"}
        version = "10.0.0" if case == "semver" else "local"
        assert calls == ([("write", f"https://fixture.invalid/{version}")] if allowed else [])
        if allowed:
            entry = runtime._mcp_manager.catalog.servers[0]
            assert entry.source.identity == "fixture@lab"
        else:
            assert not clients

    asyncio.run(scenario())
