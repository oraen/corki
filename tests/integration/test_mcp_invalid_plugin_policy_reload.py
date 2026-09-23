"""Invalid raw-layer plugin maps fall back as a whole after a live user reload."""

import asyncio
import json
import tomllib
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.config.mcp_requirements import MCPRequirements
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


@pytest.mark.parametrize("kind", ["selected_plugin"])
@pytest.mark.parametrize("mode", ["compatible", "native", "code_mode"])
@pytest.mark.parametrize("case", ["removed", "tools", "approval", "unrelated", "mapping"])
@pytest.mark.parametrize("load_plugins", [False, True])
@pytest.mark.parametrize(
    "declared,package",
    [
        ("unrestricted", "fixture"),
        ("allowlist", "fixture"),
        ("disabled", "fixture"),
        ("managed", "fixture"),
        ("unrestricted", "directories"),
        ("unrestricted", "disabled"),
    ],
)
def test_invalid_live_plugin_map_releases_selected_policy(
    tmp_path, monkeypatch, kind, mode, case, load_plugins, declared, package
):
    async def scenario():
        config = tmp_path / "config.toml"
        base_policy = f'[plugins.{package}.mcp_servers.docs]\ndisabled_tools=["write"]\n'
        consent = '\n[mcp.servers.consent]\ntransport="http"\nurl="https://fixture.invalid"\n'
        config.write_text(base_policy + consent)
        clients, calls, requests = [], [], []

        def factory(settings):
            async def respond(request):
                assert str(request.url).rstrip("/") == "https://fixture.invalid"
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
        declaration = MCPServerSettings(
            "docs",
            "http",
            url="https://fixture.invalid",
            enabled=declared != "disabled",
            enabled_tools=("read",) if declared == "allowlist" else None,
        )
        source = MCPCatalogSource(kind, package)
        catalog = MCPCatalog((MCPRegistration(declaration, source),))

        model = Model()

        async def create(admitted_settings, thread=None):
            return await LangGraphRuntime.acreate(
                settings=admitted_settings,
                model=model,
                registry=ToolRegistry(),
                database_path=tmp_path / "runtime.db",
                home_path=tmp_path / "home",
                load_plugins=load_plugins,
                mcp_catalog=catalog,
                mcp_requirements=MCPRequirements(servers=()) if declared == "managed" else None,
                thread_id=thread,
            )

        runtime = await create(settings)
        try:
            first = [event async for event in runtime.stream("Find and use POLICY_FIXTURE write")]
            assert isinstance(first[-1], TurnCompleted)
            assert calls == [], "initial valid policy must deny write"
            changed = {
                "removed": "",
                "tools": base_policy + "tools=[]\n",
                "approval": base_policy + 'default_tools_approval_mode="invalid"\n',
                "unrelated": base_policy + '[plugins.unrelated]\nenabled="invalid"\n',
                "mapping": "plugins=[]\n",
            }[case]
            config.write_text(changed + consent)
            persistence = runtime._mcp_manager._approval_persistence
            await persistence.persist(
                ("consent", None, None, "read"),
                persistence.configuration,
                None,
            )
            assert persistence.document["mcp"]["servers"]["consent"]["tools"]["read"] == {
                "approval_mode": "approve"
            }, "valid raw user layer must publish"
            assert "apps" not in persistence.document
            saved = tomllib.loads(config.read_text())
            assert "apps" not in saved
            assert saved["mcp"]["servers"]["consent"]["tools"]["read"] == {
                "approval_mode": "approve"
            }
            assert persistence.document.get("plugins", {}) == {}, "reject the whole policy map"
            model.steps = 0
            requests.clear()
            events = [event async for event in runtime.stream("Find and use POLICY_FIXTURE write")]
            assert isinstance(events[-1], TurnCompleted)
            assert calls == (["write"] if declared == "unrestricted" else []), (
                "invalid whole plugin map must release sibling policy, not declared restrictions"
            )
            if declared in {"disabled", "managed"}:
                assert not clients, "declaration and managed denials must precede transport startup"
            # A later valid document must replace the fallback, including removal
            # of definitions found during the previous Turn.
            config.write_text(base_policy + consent)
            await persistence.persist(
                ("consent", None, None, "read"),
                persistence.configuration,
                None,
            )
            assert persistence.document["plugins"][package]["mcp_servers"]["docs"] == {
                "disabled_tools": ["write"]
            }
            calls.clear()
            model.steps = 0
            repaired = [
                event async for event in runtime.stream("Find and use POLICY_FIXTURE write")
            ]
            assert isinstance(repaired[-1], TurnCompleted)
            assert calls == [], "repaired policy must revoke the temporarily discovered write tool"
        finally:
            await runtime.aclose()
        assert all(client.is_closed for client in clients)

    asyncio.run(scenario())
