"""Official Apps error metadata cannot synthesize an authorization UI."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from corki.config import CorkiSettings
from corki.mcp.manager import MCPManager


@pytest.mark.parametrize("server", ["codex_apps", "independent"])
def test_auth_metadata_does_not_prompt_or_replay(server):
    async def scenario():
        effects = []
        result = {
            "isError": True,
            "content": [],
            "_meta": {
                "_codex_apps": {
                    "connector_auth_failure": {"is_auth_failure": True, "connector_id": "mail"}
                }
            },
        }

        async def call(arguments, **kwargs):
            effects.append("call")
            return result, SimpleNamespace(connector_id="mail", connector_name="Mail")

        async def prompt(*args, **kwargs):
            effects.append("prompt")
            return {"action": "accept"}

        async def refresh(**kwargs):
            effects.append("refresh")

        @asynccontextmanager
        async def prepare(*args):
            yield SimpleNamespace(
                call_with_metadata=call,
                approval_policy="on-request",
                tool_info=SimpleNamespace(identity=SimpleNamespace(server=server)),
            )

        manager = MCPManager.__new__(MCPManager)
        manager.prepare_call = prepare
        manager._auth_elicitation = True
        manager.elicitations = SimpleNamespace(request=prompt)
        manager.hard_refresh_apps_tools = refresh
        assert await manager.call_tool(server, "read", {}, call_id="call") is result
        assert effects == ["call"]

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [True, False, None, "obsolete"])
def test_old_apps_auth_setting_is_inert(tmp_path, value):
    assert CorkiSettings(tmp_path, mcp_auth_elicitation=value).mcp_auth_elicitation is False
