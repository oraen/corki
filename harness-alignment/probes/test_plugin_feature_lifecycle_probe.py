"""Static plugin gates survive desired-catalog updates and cold history replay."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.catalog import MCPCatalog, MCPCatalogSource, MCPRegistration
from corki.mcp.client import HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("kind", ["plugin", "selected_plugin", "extension", "config"])
@pytest.mark.parametrize("mode", ["compatible", "native", "code_mode"])
@pytest.mark.parametrize("policy", ["disabled", "control"])
@pytest.mark.parametrize("lifecycle", ["catalog", "reconcile", "refresh", "cold"])
def test_plugin_feature_gate_survives_updates_and_reopen(
    tmp_path, monkeypatch, kind, mode, policy, lifecycle
):
    async def scenario():
        config = tmp_path / "config.toml"
        config.write_text(
            "[features]\nplugins="
            + ("false" if policy == "disabled" else "true")
            + "\n"
            + "[plugins.fixture.mcp_servers.docs]\n"
            + {
                "disabled": "enabled=true\n",
                "allow": 'enabled_tools=["read"]\n',
                "deny": 'disabled_tools=["write"]\n',
                "control": "enabled=true\n",
            }[policy]
        )
        clients, calls, requests = [], [], []

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
                    calls.append(packet["params"]["name"])
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
                requests.append(request)
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
        declaration = MCPServerSettings("docs", "http", url="https://fixture.invalid")
        source = MCPCatalogSource(kind, None if kind == "config" else "fixture")
        catalog = MCPCatalog((MCPRegistration(declaration, source),))

        def create(admitted_settings, thread=None):
            return LangGraphRuntime.create(
                settings=admitted_settings,
                model=Model(),
                registry=ToolRegistry(),
                database_path=tmp_path / "runtime.db",
                home_path=tmp_path / "home",
                load_plugins=False,
                mcp_catalog=catalog,
                thread_id=thread,
            )

        runtime = create(
            replace(settings, plugins_enabled=True) if lifecycle == "cold" else settings
        )
        if lifecycle == "cold":
            thread = runtime.thread_id
            try:
                first = [
                    event async for event in runtime.stream("Find and use POLICY_FIXTURE write")
                ]
                assert isinstance(first[-1], TurnCompleted)
                assert calls == ["write"]
            finally:
                await runtime.aclose()
            assert all(client.is_closed for client in clients)
            clients.clear()
            calls.clear()
            requests.clear()
            runtime = create(settings, thread)
        else:
            await runtime.mcp_tool_catalog()
            updated = replace(declaration, url="https://updated.invalid")
            if lifecycle == "catalog":
                runtime.request_mcp_catalog(MCPCatalog((MCPRegistration(updated, source),)))
            elif lifecycle == "reconcile":
                runtime.request_mcp_reconcile((updated,))
            else:
                runtime.request_mcp_refresh((updated,))
        try:
            events = [event async for event in runtime.stream("Find and use POLICY_FIXTURE write")]
        finally:
            await runtime.aclose()
        assert isinstance(events[-1], TurnCompleted)
        assert all(client.is_closed for client in clients)
        allowed = policy == "control" or kind in {"extension", "config"}
        assert calls == (["write"] if allowed else []), (kind, policy, calls)
        if not allowed:
            assert not clients, "disabled plugin server must not initialize"
            assert not any(
                "POLICY_FIXTURE write" in tool.description
                for request in requests
                for tool in request.tools
            )

    asyncio.run(scenario())
