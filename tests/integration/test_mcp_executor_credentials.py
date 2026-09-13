"""Executor capability controls who resolves a configured bearer environment variable."""

import asyncio
import base64
import json
from dataclasses import replace

import pytest
from test_mcp_bound_http import runtime_for, server
from test_mcp_executor_http import delta, envelope, executor

from corki.mcp.catalog import MCPCatalog, MCPRegistration
from corki.mcp.runtime_environment import MCPHTTPEnvironment, MCPRuntimeContext
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ToolResultItem


@pytest.mark.parametrize("capability", [None, False, True])
@pytest.mark.parametrize("local_present", [False, True])
def test_executor_bearer_capability_and_controller_fallback(
    tmp_path, monkeypatch, capability, local_present
):
    name = "CORKI_FIXTURE_EXECUTOR_TOKEN"
    if local_present:
        monkeypatch.setenv(name, "controller-only-token")
    else:
        monkeypatch.delenv(name, raising=False)

    async def scenario():
        seen, effects = [], []
        info = (
            None
            if capability is None
            else {
                "shell": {"name": "sh", "path": "/bin/sh"},
                "capabilities": {"httpHeaderEnvVars": capability},
            }
        )

        async def handle(socket, packet):
            params = packet["params"]
            seen.append(params)
            auth = [
                header for header in params["headers"] if header["name"].lower() == "authorization"
            ]
            expected = (
                {"name": "authorization", "value": "Bearer ", "valueEnvVar": name}
                if capability
                else {"name": "authorization", "value": "Bearer controller-only-token"}
            )
            assert auth == [expected]
            if capability:
                assert "controller-only-token" not in json.dumps(params)
                assert "literal-authorization" not in json.dumps(params)
            if params["method"] in ("GET", "DELETE"):
                await envelope(socket, packet, 405 if params["method"] == "GET" else 204)
                if params["streamResponse"]:
                    await delta(socket, packet, 1, done=True)
                return
            message = json.loads(base64.b64decode(params["bodyBase64"]))
            if "id" not in message:
                await envelope(socket, packet, 202)
                await delta(socket, packet, 1, done=True)
                return
            if message["method"] == "initialize":
                result = {
                    "capabilities": {},
                    "protocolVersion": "2025-06-18",
                    "serverInfo": {"name": "authenticated", "version": "1"},
                }
            elif message["method"] == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": "write",
                            "description": "needle",
                            "inputSchema": {"type": "object"},
                        }
                    ]
                }
            else:
                assert message["method"] == "tools/call"
                effects.append(message)
                if len(effects) == 1:
                    await envelope(socket, packet, 404)
                    await delta(socket, packet, 1, done=True)
                    return
                result = {"content": [{"type": "text", "text": "authenticated effect"}]}
            await envelope(
                socket,
                packet,
                headers=[
                    {"name": "content-type", "value": "application/json"},
                    {"name": "mcp-session-id", "value": "session"},
                ],
            )
            await delta(
                socket,
                packet,
                1,
                json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}).encode(),
                done=True,
            )

        async with executor(handle, environment_info=info) as (transport, packets):
            settings = replace(
                server(),
                bearer_token_env_var=name,
                headers=(("Authorization", "literal-authorization"),),
            )
            runtime = runtime_for(
                tmp_path,
                MCPRuntimeContext((MCPHTTPEnvironment("remote", transport),)),
                mode="compatible",
                catalog=MCPCatalog((MCPRegistration(settings),)),
            )
            try:
                events = [event async for event in runtime.stream("needle")]
                assert isinstance(events[-1], TurnCompleted)
                results = [
                    item
                    for item in runtime._model.requests[-1].items
                    if isinstance(item, ToolResultItem)
                ]
                assert ("authenticated effect" in repr(results)) == bool(
                    capability or local_present
                )
                if capability or local_present:
                    assert runtime._mcp_manager.warnings == () and len(effects) == 2
                else:
                    assert seen == [] and "is not set" in repr(runtime._mcp_manager.warnings)
            finally:
                await runtime.aclose()
            assert sum(packet["method"] == "environment/info" for packet in packets) == (
                capability is None
            )
            if seen:
                assert any(params["method"] == "DELETE" for params in seen)

    asyncio.run(scenario())


@pytest.mark.parametrize("capability", ["true", 1, None])
def test_invalid_executor_capability_never_falls_back_to_controller_token(
    tmp_path, monkeypatch, capability
):
    monkeypatch.setenv("CORKI_FIXTURE_EXECUTOR_TOKEN", "must-not-send")

    async def scenario():
        async def forbidden(socket, packet):
            raise AssertionError("invalid capability must be rejected before any MCP HTTP request")

        info = {
            "shell": {"name": "sh", "path": "/bin/sh"},
            "capabilities": {"httpHeaderEnvVars": capability},
        }
        async with executor(forbidden, environment_info=info) as (transport, packets):
            settings = replace(server(), bearer_token_env_var="CORKI_FIXTURE_EXECUTOR_TOKEN")
            runtime = runtime_for(
                tmp_path,
                MCPRuntimeContext((MCPHTTPEnvironment("remote", transport),)),
                catalog=MCPCatalog((MCPRegistration(settings),)),
            )
            try:
                await runtime._ensure_ready()
                await runtime._mcp_manager.refresh_if_dirty()
                assert "capability" in repr(runtime._mcp_manager.warnings)
                assert all(packet["method"] != "http/request" for packet in packets)
            finally:
                await runtime.aclose()

    asyncio.run(scenario())
