"""Installation must actively discover OAuth even when ordinary MCP works anonymously."""

import asyncio
import json

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.http_client import OwnedHTTPClient
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    "mode", ["supported", "unsupported", "unavailable", "bad_issuer", "bad_resource"]
)
def test_installation_runs_active_oauth_discovery_and_preserves_mcp(tmp_path, monkeypatch, mode):
    async def scenario():
        skill = tmp_path / ".corki/skills/discovery-guide/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: discovery-guide\ndescription: fixture\n---\nBODY")
        meta = skill.parent / "agents/openai.yaml"
        meta.parent.mkdir()
        meta.write_text(
            "dependencies:\n  tools:\n    - type: mcp\n      value: discovery\n"
            "      transport: streamable_http\n      url: https://fixture.invalid/mcp\n"
        )
        requests, clients, models = [], [], []

        def respond(request):
            assert request.url.host == "fixture.invalid"
            requests.append(request)
            if request.method == "GET":
                path = request.url.path
                if path == "/mcp":
                    return httpx.Response(405)
                if mode == "unsupported":
                    return httpx.Response(404)
                if mode == "unavailable":
                    return httpx.Response(503)
                if "oauth-protected-resource" in path:
                    return httpx.Response(
                        200,
                        json={
                            "resource": "https://fixture.invalid/other"
                            if mode == "bad_resource"
                            else "https://fixture.invalid/mcp",
                            "authorization_servers": ["https://fixture.invalid"],
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "issuer": "https://fixture.invalid/wrong"
                        if mode == "bad_issuer"
                        else "https://fixture.invalid",
                        "authorization_endpoint": "https://fixture.invalid/authorize",
                        "token_endpoint": "https://fixture.invalid/token",
                    },
                )
            if request.method == "DELETE":
                return httpx.Response(204)
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

        class Transport(httpx.MockTransport):
            def __init__(self):
                super().__init__(respond)
                self.closed = False
                clients.append(self)

            async def aclose(self):
                self.closed = True

        monkeypatch.setenv("NO_PROXY", "*")
        monkeypatch.setattr(httpx.AsyncClient, "_init_transport", lambda *a, **kw: Transport())
        monkeypatch.setattr(OwnedHTTPClient, "_init_transport", lambda *a, **kw: Transport())

        class Model:
            async def stream(self, request):
                models.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        runtime = LangGraphRuntime.create(
            settings=CorkiSettings(
                tmp_path, execution_permissions=None, tool_search_mode="disabled"
            ),
            model=Model(),
            registry=ToolRegistry(),
            home_path=tmp_path / "home",
            database_path=tmp_path / "session.db",
        )
        try:
            for text in ("$discovery-guide", "continue"):
                events = [event async for event in runtime.stream(text)]
                assert isinstance(events[-1], TurnCompleted)
            assert any(t.name == "mcp__discovery::lookup" for t in models[-1].tools)
            discovered = [
                r.url.path
                for r in requests
                if r.headers.get("mcp-protocol-version") == "2024-11-05"
            ]
            prefix = ["/mcp", "/.well-known/oauth-protected-resource/mcp"]
            if mode == "unsupported":
                expected = prefix + [
                    "/mcp/.well-known/oauth-protected-resource",
                    "/.well-known/oauth-protected-resource",
                    "/.well-known/oauth-authorization-server/mcp",
                    "/.well-known/openid-configuration/mcp",
                    "/mcp/.well-known/openid-configuration",
                    "/.well-known/oauth-authorization-server",
                ]
            elif mode == "unavailable":
                expected = prefix
            else:
                expected = prefix + [prefix[-1]]
                if mode != "bad_resource":
                    expected.append("/.well-known/oauth-authorization-server")
            assert discovered == expected, "installation never performed native active discovery"
        finally:
            await runtime.aclose()
            assert clients and all(c.closed for c in clients)

    asyncio.run(asyncio.wait_for(scenario(), 10))
