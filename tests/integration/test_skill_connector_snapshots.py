"""Connector metadata cannot suppress explicitly requested local skills."""

import asyncio

import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.catalog import MCPCatalog, MCPCatalogSource, MCPRegistration
from corki.mcp.client import MCPClient
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ContextItem
from corki.tools import ToolRegistry


@pytest.mark.parametrize("legacy_meta", [False, True])
@pytest.mark.parametrize("apps", [False, True])
def test_local_skill_survives_same_named_connector_and_refresh(
    tmp_path, monkeypatch, legacy_meta, apps
):
    async def scenario():
        path = tmp_path / ".corki/skills/gmail/SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("---\nname: gmail\ndescription: fixture\n---\nBODY")
        policy = path.parent / "agents/openai.yaml"
        policy.parent.mkdir()
        policy.write_text("dependencies:\n  tools:\n    - type: mcp\n      value: skill_dep")
        definitions = (
            {
                "name": "search",
                "inputSchema": {"type": "object"},
                "_meta": {"connector_id": "gmail-id", "connector_name": "Gmail"}
                if legacy_meta
                else {},
            },
        )
        clients = []
        phase = 0

        class Client(MCPClient):
            def __init__(self, settings):
                super().__init__(settings)
                self.closed = False
                self.phase = phase
                clients.append(self)

            async def start(self):
                pass

            async def list_tools(self):
                return list(definitions) if not self.phase else []

            async def aclose(self):
                self.closed = True

            async def _exchange(self, message):
                raise AssertionError(message)

            async def _send_notification(self, message):
                raise AssertionError(message)

        monkeypatch.setattr("corki.mcp.manager.create_client", lambda s: Client(s))
        monkeypatch.setattr("corki.mcp.manager.HttpMCPClient", lambda s, **kw: Client(s))
        requests = []

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        server = MCPServerSettings(
            "codex_apps" if apps else "ordinary", "http", url="https://fixture.invalid"
        )
        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path,
                execution_permissions=None,
                mcp_servers=(server,),
                mcp_optional_startup_grace_ms=10,
            ),
            mcp_catalog=MCPCatalog(
                (MCPRegistration(server, MCPCatalogSource("compatibility", "host")),)
            ),
            model=Model(),
            registry=ToolRegistry(),
            home_path=tmp_path / "home",
            database_path=tmp_path / "session.db",
        )
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.start()

            # The local skill remains selectable before and after catalog replacement.
            async def turn():
                return [e async for e in runtime.stream("$gmail")]

            for index in range(2):
                if index:
                    phase = 1
                    runtime.request_mcp_refresh()
                    await runtime._mcp_manager.start()
                events = await asyncio.wait_for(turn(), 2)
                assert isinstance(events[-1], TurnCompleted), events[-1]
                selected = [
                    i
                    for i in requests[-1].items
                    if isinstance(i, ContextItem)
                    and i.turn_id == events[-1].turn_id
                    and i.key.startswith("extensions.skills.selected.")
                ]
                assert len(selected) == 1
                state = await runtime._compiled.aget_state(
                    runtime._graph_config(events[-1].turn_id)
                )
                assert tuple(state.values["mcp_required_servers"]) == ("skill_dep",)
        finally:
            await runtime.aclose()
        assert all(client.closed for client in clients)

    asyncio.run(scenario())
