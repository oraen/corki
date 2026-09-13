"""Legacy orchestrator settings do not gate ordinary MCP resource access."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.mcp.tool_catalog_cache import MCPToolCatalogCache
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("enabled", [False, True, None])
@pytest.mark.parametrize(
    "operation",
    [
        "aggregate",
        "aggregate_templates",
        "page",
        "templates",
        "read",
        "aggregate_prompts",
        "prompts",
        "get_prompt",
    ],
)
def test_legacy_orchestrator_setting_preserves_all_ordinary_mcp(
    tmp_path, monkeypatch, mode, enabled, operation
):
    async def scenario():
        config = tmp_path / "config.toml"
        config.write_text(
            "[orchestrator.mcp]\n"
            if enabled is None
            else f"[orchestrator.mcp]\nenabled = {str(enabled).lower()}\n"
        )
        settings = replace(
            CorkiSettings.for_directory(tmp_path, config_file=config),
            skills_enabled=False,
            execution_permissions=None,
            tool_search_mode="disabled",
            tool_mode=mode,
            mcp_servers=tuple(
                MCPServerSettings(name, "http", url=f"https://{name}.invalid")
                for name in ("codex_apps", "ordinary")
            ),
        )
        clients, operations, outputs = [], [], []

        def factory(server):
            async def respond(request):
                packet = json.loads(request.content)
                method = packet["method"]
                if method == "notifications/initialized":
                    return httpx.Response(202)
                if method == "initialize":
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": server.name, "version": "1"},
                    }
                elif method == "tools/list":
                    result = {"tools": []}
                else:
                    operations.append((server.name, method))
                    if method == "resources/list":
                        result = {"resources": [{"uri": "fixture:value", "name": server.name}]}
                    elif method == "resources/templates/list":
                        result = {
                            "resourceTemplates": [
                                {"uriTemplate": "fixture:{id}", "name": server.name}
                            ]
                        }
                    elif method == "prompts/list":
                        result = {"prompts": [{"name": server.name}]}
                    elif method == "prompts/get":
                        result = {
                            "messages": [
                                {
                                    "role": "user",
                                    "content": {
                                        "type": "text",
                                        "text": server.name,
                                    },
                                }
                            ]
                        }
                    else:
                        assert method == "resources/read"
                        result = {"contents": [{"uri": "fixture:value", "text": server.name}]}
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
                )

            client = HttpMCPClient(server, transport=httpx.MockTransport(respond))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        name = {
            "aggregate": "list_mcp_resources",
            "aggregate_templates": "list_mcp_resource_templates",
            "page": "list_mcp_resources",
            "templates": "list_mcp_resource_templates",
            "read": "read_mcp_resource",
            "aggregate_prompts": "list_mcp_prompts",
            "prompts": "list_mcp_prompts",
            "get_prompt": "get_mcp_prompt",
        }[operation]
        aggregate = operation.startswith("aggregate")

        class Model:
            steps = 0

            async def stream(self, request):
                self.steps += 1
                turn = request.items[-1].turn_id
                if self.steps > 1:
                    outputs.append(
                        [item for item in request.items if isinstance(item, ToolResultItem)][-1]
                    )
                if self.steps <= 2:
                    if mode == "direct":
                        assert name in {tool.name for tool in request.tools}
                    else:
                        assert name in next(
                            tool.description for tool in request.tools if tool.name == "exec"
                        )
                    # Exercise both explicitly configured names through the same resource tool.
                    args = (
                        {}
                        if aggregate and self.steps == 1
                        else {"server": " codex_apps " if self.steps == 1 else "ordinary"}
                    )
                    if operation == "read":
                        args["uri"] = "fixture:value"
                    if "prompt" in operation and "server" in args:
                        args["server"] = args["server"].strip()
                    if operation == "get_prompt":
                        args["name"] = "fixture"
                    call = (
                        ToolCall(new_tool_call_id(), name, args)
                        if mode == "direct"
                        else ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments=f"text(await tools.{name}({json.dumps(args)}));",
                        )
                    )
                    yield ModelCompleted((ToolCallItem(call, turn, new_step_id()),))
                else:
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=settings,
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "orchestrator.db",
            home_path=tmp_path / "home",
            mcp_tool_catalog_cache=MCPToolCatalogCache(),
        )
        try:
            events = [event async for event in runtime.stream("inspect resources")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(outputs) == 2
            assert not outputs[1].is_error and "ordinary" in outputs[1].content
            assert not outputs[0].is_error and "codex_apps" in outputs[0].content
            assert sum(server == "codex_apps" for server, _ in operations) == 1
            assert sum(server == "ordinary" for server, _ in operations) == 1 + int(aggregate)
            # A model visibility gate must not revoke the host's connection authority.
            host_value = await runtime._mcp_manager.read_resource("codex_apps", "fixture:value")
            assert host_value["contents"][0]["text"] == "codex_apps"
        finally:
            await runtime.aclose()
        assert len(clients) == 2 and all(client.is_closed for client in clients)

    asyncio.run(scenario())
