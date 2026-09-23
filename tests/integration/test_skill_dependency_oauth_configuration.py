"""HTTP dependency callback configuration reaches the real Runtime/client catalog."""

import asyncio
import json
import tomllib

import httpx

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.mcp.client import HttpMCPClient
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.tools import ToolRegistry


def test_installed_callback_port_is_not_lost_before_http_startup(tmp_path, monkeypatch):
    async def scenario():
        skill = tmp_path / ".corki/skills/callback-guide/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: callback-guide\ndescription: fixture\n---\nBODY")
        metadata = skill.parent / "agents/openai.yaml"
        metadata.parent.mkdir()
        metadata.write_text(
            "dependencies:\n  tools:\n    - type: mcp\n      value: callback_fixture\n"
            "      transport: streamable_http\n      url: https://fixture.invalid/mcp\n"
            "      oauth:\n        callback_port: 18473\n"
        )
        clients, requests = [], []

        def factory(settings, **kwargs):
            def respond(request):
                if request.method == "DELETE":
                    return httpx.Response(204)
                if request.method == "GET":
                    return httpx.Response(405)
                packet = json.loads(request.content)
                if "id" not in packet:
                    return httpx.Response(202)
                if packet["method"] == "initialize":
                    result = {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "serverInfo": {"name": "fixture", "version": "1"},
                    }
                else:
                    assert packet["method"] == "tools/list"
                    result = {"tools": [{"name": "lookup", "inputSchema": {"type": "object"}}]}
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
                )

            client = HttpMCPClient(settings, transport=httpx.MockTransport(respond), **kwargs)
            clients.append(client)
            return client

        monkeypatch.setattr("corki.mcp.manager.HttpMCPClient", factory)
        monkeypatch.setattr("corki.mcp.manager.create_client", factory)
        # Configuration propagation is separate from OAuth discovery/login.
        monkeypatch.setattr(
            "corki.mcp.oauth_discovery.OwnedHTTPClient",
            lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request: httpx.Response(404))
            ),
        )

        class Model:
            async def stream(self, request):
                requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = await LangGraphRuntime.acreate(
            settings=CorkiSettings(
                tmp_path, execution_permissions=None, tool_search_mode="disabled"
            ),
            model=Model(),
            registry=ToolRegistry(),
            home_path=tmp_path / "home",
            database_path=tmp_path / "session.db",
        )
        try:
            for text in ("$callback-guide", "continue"):
                events = [event async for event in runtime.stream(text)]
                assert isinstance(events[-1], TurnCompleted)
            assert any(t.name == "mcp__callback_fixture::lookup" for t in requests[-1].tools)
            saved = tomllib.loads((tmp_path / "home/config.toml").read_text())
            assert saved["mcp"]["servers"]["callback_fixture"]["oauth"]["callback_port"] == 18473
            assert clients, "fixture did not reach real HTTP startup"
            for client in clients:
                oauth = getattr(client.settings, "oauth", None)
                assert oauth is not None, (
                    "persisted callback configuration was discarded before startup"
                )
                assert oauth.callback_port == 18473
            server = runtime._mcp_manager.skill_dependency_catalog().servers[0].settings
            assert server.oauth == clients[0].settings.oauth
        finally:
            await runtime.aclose()

    asyncio.run(asyncio.wait_for(scenario(), 10))
