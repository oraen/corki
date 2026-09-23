"""Configured resource entry points must not depend on an already-ready client."""

import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.mcp.tool_catalog_cache import CatalogSnapshot, MCPToolCatalogCache
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.session_source import SessionSource, SubAgentSource
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry

RESOURCE_NAMES = {"list_mcp_resources", "list_mcp_resource_templates", "read_mcp_resource"}


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("state", ["pending", "dormant", "failed"])
@pytest.mark.parametrize("operation", ["aggregate", "page", "templates", "read"])
def test_resources_exist_before_readiness_and_only_named_use_starts_dormant_server(
    tmp_path, monkeypatch, mode, state, operation
):
    async def scenario():
        clients, operations, outputs = [], [], []
        started, release, sampled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        server = MCPServerSettings("docs", "http", url="https://docs.invalid")
        cache = MCPToolCatalogCache()
        if state == "dormant":
            entry = cache.context(server)
            entry.publish_if_newest(
                entry.begin_fetch(),
                CatalogSnapshot(({"name": "lookup", "inputSchema": {"type": "object"}},), None),
            )

        def factory(settings):
            async def respond(request):
                packet = json.loads(request.content)
                method = packet["method"]
                if method == "notifications/initialized":
                    return httpx.Response(202)
                if method == "initialize":
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "serverInfo": {"name": "docs", "version": "1"},
                    }
                elif method == "tools/list":
                    started.set()
                    if state == "failed":
                        return httpx.Response(
                            200,
                            json={
                                "jsonrpc": "2.0",
                                "id": packet["id"],
                                "error": {"code": -32603, "message": "fixture startup failure"},
                            },
                        )
                    await release.wait()
                    result = {"tools": []}
                else:
                    operations.append(method)
                    if method == "resources/list":
                        result = {"resources": [{"uri": "fixture:value", "name": "live"}]}
                    elif method == "resources/templates/list":
                        result = {
                            "resourceTemplates": [{"uriTemplate": "fixture:{id}", "name": "live"}]
                        }
                    else:
                        assert method == "resources/read"
                        result = {"contents": [{"uri": "fixture:value", "text": "live"}]}
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(respond))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        steps = 0

        class Model:
            async def stream(self, request):
                nonlocal steps
                steps += 1
                turn = request.items[-1].turn_id
                if steps == 1:
                    names = {tool.name for tool in request.tools}
                    if mode == "direct":
                        assert names >= RESOURCE_NAMES
                    else:
                        assert "exec" in names and not RESOURCE_NAMES & names
                        description = next(t.description for t in request.tools if t.name == "exec")
                        assert all(name in description for name in RESOURCE_NAMES)
                        assert "list_mcp_prompts" not in description
                        assert "get_mcp_prompt" not in description
                    assert "list_mcp_prompts" not in names and "get_mcp_prompt" not in names
                    assert not runtime._mcp_manager._clients_by_name
                    assert bool(clients) is (state != "dormant")
                    sampled.set()
                    name = {
                        "aggregate": "list_mcp_resources",
                        "page": "list_mcp_resources",
                        "templates": "list_mcp_resource_templates",
                        "read": "read_mcp_resource",
                    }[operation]
                    args = {} if operation == "aggregate" else {"server": "docs"}
                    if operation == "read":
                        args["uri"] = "fixture:value"
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
                    outputs.append([i for i in request.items if isinstance(i, ToolResultItem)][-1])
                    yield ModelCompleted((AssistantMessageItem("done", turn, new_step_id()),))

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_search_mode="disabled",
                tool_mode=mode,
                mcp_optional_startup_grace_ms=1,
                mcp_servers=(server,),
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "registration.db",
            home_path=tmp_path / "home",
            mcp_tool_catalog_cache=cache,
            session_source=SessionSource.subagent(SubAgentSource("review"))
            if state == "dormant"
            else SessionSource.from_startup_arg("cli"),
        )

        async def activate_named():
            await sampled.wait()
            await started.wait()
            release.set()

        activator = (
            asyncio.create_task(activate_named())
            if operation != "aggregate" and state != "failed"
            else None
        )
        try:
            events = [event async for event in runtime.stream("inspect resources")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(outputs) == 1
            if operation == "aggregate":
                assert '"resources":[]' in outputs[0].content and not outputs[0].is_error
                assert operations == []
                assert bool(clients) is (state != "dormant")
            elif state == "failed":
                assert "unknown or unavailable MCP server" in outputs[0].content
                assert outputs[0].is_error
                assert operations == []
            else:
                assert "live" in outputs[0].content and not outputs[0].is_error
                assert operations == [
                    {
                        "page": "resources/list",
                        "templates": "resources/templates/list",
                        "read": "resources/read",
                    }[operation]
                ]
        finally:
            if activator is not None:
                activator.cancel()
                await asyncio.gather(activator, return_exceptions=True)
            await runtime.aclose()
        assert all(client.is_closed for client in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["direct", "code_mode_only"])
@pytest.mark.parametrize("policy", ["empty", "disabled", "denied"])
def test_resource_registration_does_not_enable_absent_or_forbidden_servers(
    tmp_path, monkeypatch, mode, policy
):
    async def scenario():
        def factory(_):
            pytest.fail("absent or forbidden servers must not instantiate a transport")

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)

        class Model:
            async def stream(self, request):
                names = {tool.name for tool in request.tools}
                assert not RESOURCE_NAMES & names
                if mode != "direct":
                    description = next(t.description for t in request.tools if t.name == "exec")
                    assert all(name not in description for name in RESOURCE_NAMES)
                yield ModelCompleted(
                    (AssistantMessageItem("done", request.items[-1].turn_id, new_step_id()),)
                )

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                working_directory=tmp_path,
                skills_enabled=False,
                tool_search_mode="disabled",
                tool_mode=mode,
                mcp_servers=()
                if policy == "empty"
                else (
                    MCPServerSettings(
                        "docs", "http", url="https://docs.invalid", enabled=policy != "disabled"
                    ),
                ),
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "policy.db",
            home_path=tmp_path / "home",
            mcp_tool_catalog_cache=MCPToolCatalogCache(),
            mcp_requirements={"mcp_servers": {}} if policy == "denied" else None,
        )
        try:
            events = [event async for event in runtime.stream("inspect disabled resources")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
