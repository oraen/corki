"""Legacy Apps identity never requires connector fields for ordinary tool use."""

import asyncio
from dataclasses import replace

import pytest
from test_mcp_tool_approval import Client

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.ids import new_tool_call_id
from corki.protocol.items import AssistantMessageItem, ToolCallItem, ToolResultItem, new_step_id
from corki.protocol.tools import ToolCall
from corki.tools import ToolRegistry


@pytest.mark.parametrize("name", ["docs", "codex_apps"])
@pytest.mark.parametrize("apps_enabled", [False, True])
@pytest.mark.parametrize("legacy_meta", [False, True])
@pytest.mark.parametrize("orchestrator_enabled", [False, True])
def test_ordinary_search_call_observation(
    tmp_path, monkeypatch, name, apps_enabled, legacy_meta, orchestrator_enabled
):
    async def scenario():
        clients = []

        class OrdinaryClient(Client):
            async def list_tools(self):
                definitions = await super().list_tools()
                if legacy_meta:
                    definitions[0]["_meta"] = {
                        "connector_id": "legacy",
                        "connector_name": "Renamed",
                        "_codex_apps": {"requires_explicit_link_id": True},
                    }
                return definitions

        def factory(settings):
            client = OrdinaryClient(settings)
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.create_client", factory)

        class Model:
            count = 0

            async def stream(self, request):
                self.count += 1
                turn, step = request.items[-1].turn_id, new_step_id()
                target = f"mcp__{name}::write"
                if self.count == 1:
                    assert target not in {t.name for t in request.tools}
                    call = ToolCall(new_tool_call_id(), "tool_search", {"query": "needle"})
                elif self.count == 2:
                    assert target in {t.name for t in request.tools}
                    call = ToolCall(new_tool_call_id(), target, {"value": 1})
                else:
                    result = [i for i in request.items if isinstance(i, ToolResultItem)][-1]
                    assert not result.is_error and "remote effect" in result.content
                    yield ModelCompleted((AssistantMessageItem("done", turn, step),))
                    return
                yield ModelCompleted((ToolCallItem(call, turn, step),))

            async def aclose(self):
                pass

        config = tmp_path / "config.toml"
        config.write_text(f"[features]\napps={str(apps_enabled).lower()}\n")
        runtime = await LangGraphRuntime.acreate(
            settings=replace(
                CorkiSettings.for_directory(tmp_path, config_file=config),
                skills_enabled=False,
                orchestrator_mcp_enabled=orchestrator_enabled,
                tool_search_mode="compatible",
                mcp_servers=(MCPServerSettings(name, "http", url="https://fixture.invalid"),),
            ),
            model=Model(),
            registry=ToolRegistry(),
            database_path=tmp_path / "history.db",
            home_path=tmp_path / "home",
        )
        try:
            events = [event async for event in runtime.stream("find needle and write")]
            assert isinstance(events[-1], TurnCompleted), events[-1]
            assert clients[0].calls == [("write", {"value": 1})]
        finally:
            await runtime.aclose()
        assert all(client.closed for client in clients)

    asyncio.run(scenario())
