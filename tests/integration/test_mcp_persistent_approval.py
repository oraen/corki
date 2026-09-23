"""Persistent MCP decisions survive sessions; failed writes retain session consent."""

import asyncio
import json
import tomllib
from dataclasses import replace

import httpx
import pytest

from corki.cli.elicitation import collect_elicitation
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


@pytest.mark.parametrize("mode", ["compatible", "native", "code_mode"])
@pytest.mark.parametrize(
    "case",
    [
        "legacy_name",
        "ordinary",
        "write_failure",
        "session",
        "prompt",
        "writes",
        "feature_off",
        "cli",
        "plugin",
        "plugin_package",
        "selected_plugin",
        "project",
        "live_reload",
        "host_override",
    ],
)
def test_persistent_decision_and_write_failure_fallback(tmp_path, monkeypatch, mode, case):
    async def scenario():
        server = (
            "ordinary"
            if case in {"ordinary", "plugin", "plugin_package", "selected_plugin", "project"}
            else "codex_apps"
        )
        config = tmp_path / "config.toml"
        cwd = tmp_path
        catalog = None
        declaration = (
            f'[mcp.servers.{server}]\ntransport = "http"\nurl = "https://fixture.invalid"\n'
        )
        original = (
            "# preserve this comment and unrelated configuration\n"
            '[unrelated]\nvalue = "keep"\n'
            '[mcp]\napproval_policy = "on-request"\n'
            + (
                declaration
                if case not in {"project", "plugin", "plugin_package", "selected_plugin"}
                else ""
            )
        )
        if case == "project":
            cwd = tmp_path / "repo"
            (cwd / ".git").mkdir(parents=True)
            (cwd / ".git/HEAD").write_text("ref: refs/heads/main\n")
            (cwd / ".corki").mkdir()
            (cwd / ".corki/config.toml").write_text(declaration)
            original += f'[projects.{json.dumps(str(cwd))}]\ntrust_level="trusted"\n'
        if case in {"plugin", "selected_plugin"}:
            catalog = MCPCatalog(
                (
                    MCPRegistration(
                        MCPServerSettings(server, "http", url="https://fixture.invalid"),
                        MCPCatalogSource(case, "fixture_plugin"),
                    ),
                )
            )
        if case == "plugin_package":
            manifest = tmp_path / "home/plugins/fixture_plugin/.codex-plugin/plugin.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(
                json.dumps(
                    {
                        "name": "fixture_plugin",
                        "description": "Persistent approval package",
                        "mcpServers": {server: {"type": "http", "url": "https://fixture.invalid"}},
                    }
                )
            )
        if case in {"prompt", "writes"}:
            original += f'[mcp.servers.{server}.tools.write]\napproval_mode="{case}"\n'
        if case == "feature_off":
            original += "[features]\ntool_call_mcp_elicitation=false\n"
        config.write_text(original)
        clients, calls, prompts, observations, endings = [], [], [], [], []
        nested = mode == "code_mode"

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
                                "name": raw,
                                "description": f"Persistent approval fixture {raw}",
                                "inputSchema": {"type": "object"},
                                "annotations": {"readOnlyHint": raw == "read"},
                                "_meta": {
                                    "connector_id": "mail",
                                    "_codex_apps": {"requires_explicit_link_id": True},
                                },
                            }
                            for raw in (
                                ("write", "read") if case == "host_override" else ("write",)
                            )
                        ]
                    }
                else:
                    assert packet["method"] == "tools/call"
                    calls.append(packet["params"])
                    result = {"content": [{"type": "text", "text": "business result"}]}
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
                first_call = 1 if nested else 2
                if self.steps == 1 and not nested:
                    call = ToolCall(
                        new_tool_call_id(), "tool_search", {"query": "Persistent approval fixture"}
                    )
                elif (
                    self.steps == first_call
                    or (
                        case in {"write_failure", "live_reload", "host_override"}
                        and self.steps == first_call + 1
                    )
                    or (case == "host_override" and self.steps == first_call + 2)
                ):
                    if self.steps > first_call:
                        observations.append(
                            [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                        )
                    description = "Persistent approval fixture " + (
                        "read"
                        if case == "host_override" and self.steps == first_call + 1
                        else "write"
                    )
                    if nested:
                        call = ToolCall(
                            new_tool_call_id(),
                            "exec",
                            None,
                            input_kind="freeform",
                            raw_arguments="const t=ALL_TOOLS.find(t=>t.description.includes("
                            + json.dumps(description)
                            + "));text(await tools[t.name]("
                            + json.dumps(
                                {
                                    "link_id": "changed"
                                    if case in {"live_reload", "host_override"}
                                    and self.steps > first_call
                                    else "account"
                                }
                            )
                            + "));",
                        )
                    else:
                        found = [i for i in request.items if getattr(i, "discovered_tools", ())][-1]
                        call = ToolCall(
                            new_tool_call_id(),
                            next(
                                t.name
                                for t in found.discovered_tools
                                if description in t.description
                            ),
                            {
                                "link_id": "changed"
                                if case in {"live_reload", "host_override"}
                                and self.steps > first_call
                                else "account"
                            },
                        )
                else:
                    observations.append(
                        [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    )
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        for session in range(1 if case in {"write_failure", "live_reload", "host_override"} else 2):
            settings = replace(
                CorkiSettings.for_directory(cwd, config_file=config),
                execution_permissions=None,
                skills_enabled=False,
                api_mode="responses",
                model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
                tool_search_mode="disabled" if nested else mode,
                tool_mode="code_mode_only" if nested else "direct",
            )
            if case == "host_override":
                settings = replace(
                    settings,
                    mcp_servers=tuple(
                        replace(s, tool_approval_modes=(("read", "prompt"),))
                        for s in settings.mcp_servers
                    ),
                )
            runtime = await LangGraphRuntime.acreate(
                settings=settings,
                model=Model(),
                registry=ToolRegistry(),
                database_path=tmp_path / f"session-{session}.db",
                home_path=tmp_path / "home",
                mcp_catalog=catalog,
            )

            async def host(request, runtime=runtime):
                prompts.append(request)
                first = len(prompts) == 1
                if first and case == "write_failure":
                    # Invalid existing TOML must not be overwritten by a grant writer.
                    config.write_text("invalid = [")
                if case == "cli" and first:
                    answers = iter(["false", "always", "accept"])

                    async def read(_):
                        return next(answers)

                    action, content = await collect_elicitation(request, read, lambda _: None)
                    runtime.respond_mcp_elicitation(
                        request.server_name, request.request_id, action, content=content
                    )
                    return
                runtime.respond_mcp_elicitation(
                    request.server_name,
                    request.request_id,
                    "accept" if first else "decline",
                    meta={"persist": "session" if case == "session" else "always"},
                )

            runtime.set_mcp_elicitation_handler(host)
            try:
                events = [event async for event in runtime.stream("write after approval")]
                endings.append(events[-1])
            finally:
                await runtime.aclose()

        assert all(client.is_closed for client in clients)
        assert all(isinstance(event, TurnCompleted) for event in endings), endings
        transient = case in {"session", "prompt", "writes", "feature_off", "selected_plugin"}
        restricted = transient or case == "host_override"
        assert (len(prompts), len(calls)) == (
            (2, 2) if case == "host_override" else (2, 1) if restricted else (1, 2)
        )
        assert len(observations) == (3 if case == "host_override" else 2)
        assert "business result" in observations[0].content
        assert ("business result" in observations[1].content) is (not restricted)
        if case == "host_override":
            assert "business result" in observations[2].content
        if case == "write_failure":
            assert config.read_text() == "invalid = ["
        elif transient:
            assert config.read_text() == original
        else:
            document = tomllib.loads(config.read_text())
            assert document["unrelated"] == {"value": "keep"}
            assert "# preserve this comment" in config.read_text()
            if case == "project":
                assert config.read_text() == original
                tool = tomllib.loads((cwd / ".corki/config.toml").read_text())["mcp"]["servers"][
                    server
                ]["tools"]["write"]
            elif case in {"plugin", "plugin_package"}:
                tool = document["plugins"]["fixture_plugin"]["mcp_servers"][server]["tools"][
                    "write"
                ]
            else:
                tool = document["mcp"]["servers"][server]["tools"]["write"]
            assert tool["approval_mode"] == "approve"

    asyncio.run(scenario())
