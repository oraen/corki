"""Legacy user reload must update live plugin/skill contributions, not just MCP consent."""

import asyncio
import json
import tomllib
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings
from corki.context.world_state import snapshot_content
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.context import ModelContextInfo
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import (
    AssistantMessageItem,
    ContextItem,
    ToolCallItem,
    ToolResultItem,
    new_step_id,
)
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("mode", ["compatible", "native", "code_mode"])
@pytest.mark.parametrize("change", ["plugin", "skill", "unchanged", "bad_write"])
def test_approval_reload_updates_next_turn_plugin_and_skill_views(
    tmp_path, monkeypatch, mode, change
):
    async def scenario():
        home = tmp_path / "home"
        package = home / "plugins/reload_fixture"
        manifest = package / ".codex-plugin/plugin.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps(
                {
                    "name": "reload_fixture",
                    "description": "Reload plugin fixture",
                    "mcpServers": {"docs": {"type": "http", "url": "https://docs.invalid"}},
                }
            )
        )
        skill = package / "skills/guide/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            "---\nname: guide\ndescription: RELOAD_SKILL_MARKER\n---\nFixture guide.\n"
        )
        config = tmp_path / "config.toml"
        original = (
            '[mcp]\napproval_policy="on-request"\n'
            '[mcp.servers.codex_apps]\ntransport="http"\nurl="https://apps.invalid"\n'
        )
        config.write_text(original)
        clients, calls, reviews, requests, observations = [], [], [], [], []
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
                    app = settings.name == "codex_apps"
                    result = {
                        "tools": [
                            {
                                "name": "write" if app else "read",
                                "description": "approval reload trigger"
                                if app
                                else "plugin reload document",
                                "inputSchema": {"type": "object"},
                                "annotations": {"readOnlyHint": not app},
                                "_meta": {"connector_id": "fixture"} if app else {},
                            }
                        ]
                    }
                else:
                    assert packet["method"] == "tools/call"
                    calls.append((settings.name, packet["params"]["name"]))
                    result = {"content": [{"type": "text", "text": "fixture result"}]}
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(respond))
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)

        class Model:
            phase = 1
            steps = 0

            async def stream(self, request):
                self.steps += 1
                requests.append((self.phase, request))
                turn, step = request.items[-1].turn_id, new_step_id()
                description = (
                    "approval reload trigger" if self.phase == 1 else "plugin reload document"
                )
                call = None
                if nested and self.steps == 1:
                    code = (
                        "const s=ALL_TOOLS.find(t=>t.name.endsWith('skill_list'));"
                        "text(await tools[s.name]({}));"
                        if self.phase == 2
                        else ""
                    )
                    code += (
                        "const t=ALL_TOOLS.find(t=>t.description.includes("
                        + json.dumps(description)
                        + "));if(t)text(await tools[t.name]({}));"
                    )
                    call = ToolCall(
                        new_tool_call_id(), "exec", None, input_kind="freeform", raw_arguments=code
                    )
                elif not nested:
                    search_step = 1 if self.phase == 1 else 2
                    if self.phase == 2 and self.steps == 1:
                        call = ToolCall(new_tool_call_id(), "skill_list", {})
                    elif self.steps == search_step:
                        call = ToolCall(new_tool_call_id(), "tool_search", {"query": description})
                    elif self.steps == search_step + 1:
                        results = [i for i in request.items if isinstance(i, ToolResultItem)]
                        found = next(
                            (
                                t
                                for t in results[-1].discovered_tools
                                if description in t.description
                            ),
                            None,
                        )
                        if found is not None:
                            call = ToolCall(new_tool_call_id(), found.name, {})
                if call is not None:
                    yield ModelCompleted((ToolCallItem(call, turn, step),))
                else:
                    observations.extend(
                        i
                        for i in request.items
                        if isinstance(i, ToolResultItem) and i.turn_id == turn
                    )
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))

            async def aclose(self):
                pass

        model = Model()
        settings = replace(
            CorkiSettings.for_directory(tmp_path, config_file=config),
            execution_permissions=None,
            api_mode="responses",
            model_contexts=(ModelContextInfo("gpt-5", supports_search_tool=True),),
            tool_search_mode="disabled" if nested else mode,
            tool_mode="code_mode_only" if nested else "direct",
        )
        runtime = LangGraphRuntime.create(
            settings=settings,
            model=model,
            registry=ToolRegistry(),
            database_path=tmp_path / "runtime.db",
            home_path=home,
        )

        async def host(request):
            reviews.append(request)
            extra = (
                '[plugins]\ndisabled=["reload_fixture"]\n'
                if change == "plugin"
                else '[[skills.config]]\nname="reload_fixture:guide"\nenabled=false\n'
                if change == "skill"
                else ""
            )
            config.write_text("invalid=[" if change == "bad_write" else original + extra)
            runtime.respond_mcp_elicitation(
                request.server_name, request.request_id, "accept", meta={"persist": "always"}
            )

        runtime.set_mcp_elicitation_handler(host)
        try:
            first = [event async for event in runtime.stream("run the approval reload trigger")]
            model.phase, model.steps = 2, 0
            second = [
                event
                async for event in runtime.stream("list skills and search the plugin document")
            ]
            catalog = await runtime.mcp_tool_catalog()
        finally:
            await runtime.aclose()
        assert all(client.is_closed for client in clients)
        assert isinstance(first[-1], TurnCompleted) and isinstance(second[-1], TurnCompleted), (
            first[-1],
            second[-1],
        )
        assert len(reviews) == 1
        catalogs = [
            (
                phase,
                snapshot_content(
                    next(
                        i
                        for i in reversed(request.items)
                        if isinstance(i, ContextItem) and i.key == "extensions.skills.catalog"
                    )
                ),
            )
            for phase, request in requests
        ]
        assert "RELOAD_SKILL_MARKER" in catalogs[0][1]
        skill_visible = change not in {"plugin", "skill"}
        if change == "bad_write":
            assert config.read_text() == "invalid=["
        else:
            assert (
                tomllib.loads(config.read_text())["apps"]["fixture"]["tools"]["write"][
                    "approval_mode"
                ]
                == "approve"
            )
        actual = {
            "model_skill": "RELOAD_SKILL_MARKER" in catalogs[-1][1],
            "tool_skill": any(
                "reload_fixture:guide" in i.content
                for i in observations
                if i.tool_name == "skill_list" or nested
            ),
            "plugin_rpc": ("docs", "read") in calls,
            "plugin_catalog": any(entry.server_name == "docs" for entry in catalog),
        }
        if change in {"plugin", "skill"}:
            # The same saved configuration must work at cold startup. This
            # separates missing live publication from unsupported config syntax.
            cold_model = Model()
            cold_model.phase = 2
            cold_settings = replace(
                CorkiSettings.for_directory(tmp_path, config_file=config),
                execution_permissions=None,
                api_mode=settings.api_mode,
                model_contexts=settings.model_contexts,
                tool_search_mode=settings.tool_search_mode,
                tool_mode=settings.tool_mode,
            )
            cold = LangGraphRuntime.create(
                settings=cold_settings,
                model=cold_model,
                registry=ToolRegistry(),
                database_path=tmp_path / "cold.db",
                home_path=home,
            )
            before_cold = len(calls)
            try:
                cold_events = [
                    event
                    async for event in cold.stream("list skills and search the plugin document")
                ]
                cold_catalog = await cold.mcp_tool_catalog()
            finally:
                await cold.aclose()
            assert isinstance(cold_events[-1], TurnCompleted), cold_events[-1]
            cold_skill = next(
                i
                for i in reversed(requests[-1][1].items)
                if isinstance(i, ContextItem) and i.key == "extensions.skills.catalog"
            )
            assert "RELOAD_SKILL_MARKER" not in snapshot_content(cold_skill)
            assert any(entry.server_name == "docs" for entry in cold_catalog) is (change == "skill")
            assert (("docs", "read") in calls[before_cold:]) is (change == "skill")
            assert all(client.is_closed for client in clients)
        assert actual == {
            "model_skill": skill_visible,
            "tool_skill": skill_visible,
            "plugin_rpc": change != "plugin",
            "plugin_catalog": change != "plugin",
        }

    asyncio.run(scenario())
