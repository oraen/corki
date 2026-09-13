"""An accessible Apps connector blocks only ambiguous plain host skill mentions."""

import asyncio

import pytest

from corki.config import CorkiSettings, MCPServerSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import MCPClient
from corki.models import ModelCompleted
from corki.protocol import InputMention
from corki.protocol.events import TurnCompleted
from corki.protocol.items import ContextItem
from corki.tools import ToolRegistry


@pytest.mark.parametrize("apps_enabled", [False, True])
@pytest.mark.parametrize("explicit_path", [False, True])
def test_runtime_skill_connector_name_conflict(tmp_path, monkeypatch, apps_enabled, explicit_path):
    async def scenario():
        path = tmp_path / ".corki/skills/gmail/SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("---\nname: gmail\ndescription: fixture\n---\nHOST_SKILL_BODY")
        policy = path.parent / "agents/openai.yaml"
        policy.parent.mkdir()
        policy.write_text("dependencies:\n  tools:\n    - type: mcp\n      value: skill_dependency")
        settings = MCPServerSettings("codex_apps", "http", url="https://fixture.invalid")

        class Client(MCPClient):
            async def start(self):
                pass

            async def list_tools(self):
                return [
                    {
                        "name": "Gmail_Search",
                        "inputSchema": {"type": "object"},
                        "_meta": {"connector_id": "gmail-id", "connector_name": "Gmail"},
                    }
                ]

            async def aclose(self):
                pass

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

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                tmp_path,
                execution_permissions=None,
                mcp_servers=(settings,),
                apps_enabled=apps_enabled,
            ),
            model=Model(),
            registry=ToolRegistry(),
            home_path=tmp_path / "home",
            database_path=tmp_path / "session.db",
        )
        try:
            await runtime._ensure_ready()
            await runtime._mcp_manager.start()
            if apps_enabled:
                catalog = await runtime._mcp_manager.list_tool_catalog()
                assert any(
                    e.connector_id == "gmail-id" and e.connector_name == "Gmail" for e in catalog
                )
            mentions = (InputMention("gmail", str(path), "skill"),) if explicit_path else ()
            events = [e async for e in runtime.stream("$gmail", mentions=mentions)]
            assert isinstance(events[-1], TurnCompleted)
            selected = [
                i
                for i in requests[0].items
                if isinstance(i, ContextItem) and i.key.startswith("extensions.skills.selected.")
            ]
            expected = explicit_path or not apps_enabled
            assert bool(selected) is expected
            state = await runtime._compiled.aget_state(runtime._graph_config(events[-1].turn_id))
            assert tuple(state.values["mcp_required_servers"]) == (
                ("skill_dependency",) if expected else ()
            )
        finally:
            await runtime.aclose()

    asyncio.run(scenario())
