"""Native plugin policies must govern the actual discovery/call/observation loop."""

import asyncio
import json
import sqlite3
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.approval_persistence import approval_settings
from corki.mcp.catalog import MCPCatalog, MCPCatalogSource, MCPRegistration
from corki.mcp.client import HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("kind", ["plugin", "selected_plugin"])
@pytest.mark.parametrize("mode", ["compatible", "native", "code_mode"])
@pytest.mark.parametrize("package", ["fixture", "directories", "disabled"])
@pytest.mark.parametrize(
    "policy",
    [
        "disabled",
        "allow",
        "deny",
        "control",
        "reenable_manifest",
        "allow_override",
        "deny_override",
        "limit",
    ],
)
def test_plugin_policy_filters_the_real_search_call_loop(
    tmp_path, monkeypatch, kind, mode, policy, package
):
    async def scenario():
        config = tmp_path / "config.toml"
        config.write_text(
            f"[plugins.{package}.mcp_servers.docs]\n"
            + {
                "disabled": "enabled=false\n",
                "allow": 'enabled_tools=["read"]\n',
                "deny": 'disabled_tools=["write"]\n',
                "control": "enabled=true\n",
                "reenable_manifest": "enabled=true\n",
                "allow_override": 'enabled_tools=["write"]\n',
                "deny_override": "disabled_tools=[]\n",
                "limit": (
                    f"[plugins.{package}.mcp_servers.docs.tools.write]\noutput_token_limit=20\n"
                ),
            }[policy]
        )
        clients, calls, observations = [], [], []

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
                    result = {
                        "content": [
                            {
                                "type": "text",
                                "text": "abcdefghij" * 200
                                if policy == "limit"
                                else "fixture result",
                            }
                        ]
                    }
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
                    observations.extend(i for i in request.items if isinstance(i, ToolResultItem))
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
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "runtime.db",
            home_path=tmp_path / "home",
            load_plugins=False,
            mcp_catalog=MCPCatalog(
                (
                    MCPRegistration(
                        MCPServerSettings(
                            "docs",
                            "http",
                            url="https://fixture.invalid",
                            enabled=policy != "reenable_manifest",
                            enabled_tools=("read",) if policy == "allow_override" else None,
                            disabled_tools=("write",) if policy == "deny_override" else None,
                        ),
                        MCPCatalogSource(kind, package),
                    ),
                )
            ),
        )
        try:
            events = [event async for event in runtime.stream("Find and use POLICY_FIXTURE write")]
        finally:
            await runtime.aclose()
        assert isinstance(events[-1], TurnCompleted)
        assert all(client.is_closed for client in clients)
        allowed = policy in {"control", "limit"} or (
            kind == "plugin" and policy in {"reenable_manifest", "allow_override", "deny_override"}
        )
        assert calls == (["write"] if allowed else []), (policy, calls)
        if policy == "limit":
            result = observations[-1].content
            if mode == "code_mode":
                # Native CodeMode sees the complete value; exec presentation has
                # its own budget. The nested MCP history still uses its policy.
                assert "abcdefghij" * 200 in result
            else:
                assert "truncated" in result and len(result) < 1000, result
            with sqlite3.connect(tmp_path / "runtime.db") as connection:
                rows = connection.execute(
                    "SELECT result_json FROM tool_executions WHERE tool_name != 'exec'"
                ).fetchall()
            ledger = next(json.loads(row[0]) for row in rows if "abcdefghij" in row[0])
            assert ledger["fallback_token_limit_override"] == 24
            assert "truncated" in ledger["content"] and len(ledger["content"]) < 200
            assert ledger["code_mode_output"]["value"]["content"][0]["text"] == "abcdefghij" * 200
        if policy == "disabled":
            assert not clients, "disabled plugin server must not initialize"

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["plugin", "selected_plugin"])
@pytest.mark.parametrize("declared,requested,expected", [(None, 5, 5), (10, 5, 5), (5, 10, 5)])
def test_plugin_policy_output_limit_is_the_minimum(kind, declared, requested, expected):
    settings = MCPServerSettings(
        "docs",
        "http",
        url="https://fixture.invalid",
        tool_output_token_limits=() if declared is None else (("read", declared),),
    )
    document = {
        "plugins": {
            "fixture": {
                "mcp_servers": {"docs": {"tools": {"read": {"output_token_limit": requested}}}}
            }
        }
    }
    actual = approval_settings(settings, document, MCPCatalogSource(kind, "fixture"))
    assert actual.tool_output_token_limits == (("read", expected),)
