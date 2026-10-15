"""A real WebSocket executor sees only individually approved redirect hops."""

import asyncio
import base64
import json
from dataclasses import replace

import httpx
import pytest
from test_mcp_bound_http import runtime_for, server
from test_mcp_executor_http import delta, envelope, executor
from test_mcp_redirect_policy import RedirectingCarrier

from corki.mcp.catalog import MCPCatalog, MCPRegistration
from corki.mcp.runtime_environment import MCPHTTPEnvironment, MCPRuntimeContext
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ToolResultItem


@pytest.mark.parametrize("mode", ["direct", "native", "compatible", "code_mode"])
@pytest.mark.parametrize("remote_bearer", [False, True])
def test_executor_redirects_keep_credentials_deadlines_ids_and_single_effect(
    tmp_path, monkeypatch, mode, remote_bearer
):
    monkeypatch.delenv("CORKI_REDIRECT_EXECUTOR_TOKEN", raising=False)

    async def scenario():
        carrier = RedirectingCarrier()

        async def handle(socket, packet):
            params = packet["params"]
            assert params["redirectPolicy"] == "stop"
            if remote_bearer:
                assert [h for h in params["headers"] if h["name"].lower() == "authorization"] == [
                    {
                        "name": "authorization",
                        "value": "Bearer ",
                        "valueEnvVar": "CORKI_REDIRECT_EXECUTOR_TOKEN",
                    }
                ]
            request = httpx.Request(
                params["method"],
                params["url"],
                headers=[(h["name"], h["value"]) for h in params["headers"]],
                content=base64.b64decode(params["bodyBase64"] or ""),
            )
            response = await carrier.handle_async_request(request)
            headers = [
                {"name": key, "value": value} for key, value in response.headers.multi_items()
            ]
            if params["streamResponse"]:
                await envelope(socket, packet, response.status_code, headers)
                if response.status_code == 307:
                    # An unfinished redirect body must not keep its client route alive.
                    await response.aclose()
                    return
                sequence = 0
                async for chunk in response.aiter_bytes():
                    sequence += 1
                    await delta(socket, packet, sequence, chunk)
                await delta(socket, packet, sequence + 1, done=True)
            else:
                await envelope(
                    socket, packet, response.status_code, headers, await response.aread()
                )
            await response.aclose()

        async with executor(
            handle, environment_info={"capabilities": {"httpHeaderEnvVars": True}}
        ) as (transport, packets):
            config = replace(
                server(),
                bearer_token_env_var="CORKI_REDIRECT_EXECUTOR_TOKEN" if remote_bearer else None,
            )
            runtime = await runtime_for(
                tmp_path,
                MCPRuntimeContext((MCPHTTPEnvironment("remote", transport),)),
                mode=mode,
                catalog=MCPCatalog((MCPRegistration(config),)),
            )
            try:
                events = [event async for event in runtime.stream("needle")]
                assert isinstance(events[-1], TurnCompleted)
                assert runtime._mcp_manager.warnings == ()
                assert "first remote effect" in repr(
                    [
                        item
                        for item in runtime._model.requests[-1].items
                        if isinstance(item, ToolResultItem)
                    ]
                )
                assert len(carrier.calls) == 1
            finally:
                await runtime.aclose()
            requests = [p["params"] for p in packets if p["method"] == "http/request"]
            assert len({r["requestId"] for r in requests}) == len(requests)
            assert any(r["method"] == "DELETE" and r["url"].endswith("/final") for r in requests)
            initialized = [
                r
                for r in requests
                if r["bodyBase64"]
                and json.loads(base64.b64decode(r["bodyBase64"])).get("method") == "initialize"
            ]
            assert len(initialized) == 2
            assert 0 < initialized[1]["timeoutMs"] <= initialized[0]["timeoutMs"]
            assert not transport.is_closed and not transport._bodies.routes
        await carrier.aclose()

    asyncio.run(scenario())
