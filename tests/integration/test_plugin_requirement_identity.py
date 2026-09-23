"""Installed plugin IDs, not shared namespaces, determine required MCP startup."""

import asyncio
import json
from dataclasses import replace

import pytest
from test_mcp_pending_startup import PendingClient

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.plugins.manifest_path import AGENT_SCHEMA
from corki.protocol import InputMention
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("agent", [False, True], ids=["legacy", "agent"])
@pytest.mark.parametrize("selection", ["skill_path", "skill_link", "plugin_path", "plugin_link"])
def test_only_selected_installation_blocks_sampling(tmp_path, monkeypatch, agent, selection):
    async def scenario():
        home = tmp_path / "home"
        skill_paths = {}
        for market in ("first", "second"):
            root = home / "plugins/cache" / market / "fixture/local"
            path = root / ("plugin.json" if agent else ".codex-plugin/plugin.json")
            path.parent.mkdir(parents=True)
            servers = {f"{market}_docs": {"url": f"https://{market}.test"}}
            path.write_text(
                json.dumps(
                    {
                        "name": "fixture",
                        **({"$schema": AGENT_SCHEMA} if agent else {}),
                        **({} if agent else {"mcpServers": servers}),
                    }
                )
            )
            if agent:
                (root / "mcp.json").write_text(
                    json.dumps(
                        {
                            "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
                            "mcpServers": {
                                name: {"type": "streamable-http", **server}
                                for name, server in servers.items()
                            },
                        }
                    )
                )
            skill = root / "skills/guide/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("---\nname: guide\ndescription: fixture\n---\nBODY_" + market)
            skill_paths[market] = skill
        config = tmp_path / "config.toml"
        config.write_text(
            '[plugins."fixture@first"]\nenabled=true\n[plugins."fixture@second"]\nenabled=true\n'
        )
        clients, requests = {}, []

        def client_factory(settings, **kwargs):
            if kwargs:
                assert kwargs["agent_plugin"] is agent
            client = PendingClient(settings)
            clients[settings.name] = client
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", client_factory)
        monkeypatch.setattr("corki.mcp.manager.HttpMCPClient", client_factory)

        class Model:
            async def stream(self, request):
                requests.append(request)
                if len(requests) == 1:
                    names = {spec.name for spec in request.tools}
                    assert "mcp__second_docs::lookup" in names
                    assert "mcp__first_docs::lookup" not in names
                    yield ModelCompleted(
                        (
                            ToolCallItem(
                                ToolCall(new_tool_call_id(), "mcp__second_docs::lookup", {}),
                                request.items[-1].turn_id,
                                new_step_id(),
                            ),
                        )
                    )
                else:
                    results = [i for i in request.items if isinstance(i, ToolResultItem)]
                    assert len(results) == 1
                    assert results[0].tool_name == "mcp__second_docs::lookup"
                    assert not results[0].is_error and "found" in results[0].content
                    yield ModelCompleted(())

            async def aclose(self):
                pass

        settings = replace(
            CorkiSettings.for_directory(tmp_path, config_file=config),
            execution_permissions=None,
            mcp_optional_startup_grace_ms=10,
            tool_search_mode="disabled",
            tool_mode="direct",
        )
        runtime = await LangGraphRuntime.acreate(
            settings=settings,
            model=Model(),
            registry=ToolRegistry(),
            home_path=home,
            database_path=tmp_path / "session.db",
        )
        locator = (
            str(skill_paths["second"])
            if selection.startswith("skill")
            else "plugin://fixture@second"
        )
        sigil = "$" if selection.startswith("skill") else "@"
        text = (
            f"[{sigil}fixture:guide]({locator})" if selection.endswith("link") else "use selected"
        )
        mentions = (
            ()
            if selection.endswith("link")
            else (
                InputMention(
                    "fixture:guide",
                    locator,
                    "skill" if selection.startswith("skill") else "mention",
                ),
            )
        )

        async def consume():
            return [event async for event in runtime.stream(text, mentions=mentions)]

        async def started():
            while set(clients) != {"first_docs", "second_docs"}:
                await asyncio.sleep(0.001)
            await asyncio.gather(*(client.entered.wait() for client in clients.values()))

        task = asyncio.create_task(consume())
        try:
            try:
                await asyncio.wait_for(started(), 3)
            except TimeoutError:
                pytest.fail(
                    f"startup clients={tuple(clients)}; "
                    f"MCP warnings={runtime._mcp_manager.warnings}; "
                    f"plugins={runtime._plugin_manager.plugins}"
                )
            await asyncio.sleep(0.08)
            assert not requests, "selected installation must finish startup before sampling"
            clients["second_docs"].release.set()
            events = await asyncio.wait_for(task, 2)
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert len(requests) == 2
            assert clients["second_docs"].calls == ["lookup"]
            assert not clients["first_docs"].listed.is_set()
            assert clients["first_docs"].calls == []
            checkpoint = await runtime._compiled.aget_state(
                runtime._graph_config(events[-1].turn_id)
            )
            assert tuple(checkpoint.values["mcp_required_servers"]) == ("second_docs",)
            assert tuple(checkpoint.values["mcp_required_plugins"]) == (
                ("fixture@second",) if selection.startswith("plugin") else ()
            )
        finally:
            for client in clients.values():
                client.release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await runtime.aclose()
        assert all(client.closed for client in clients.values())

    asyncio.run(scenario())
