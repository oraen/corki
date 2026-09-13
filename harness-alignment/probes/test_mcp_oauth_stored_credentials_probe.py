"""Cold Runtime must consume File credentials under native issuer rules.

Synthetic credentials are written only under tmp_path. Every HTTP request uses
an in-memory carrier, and neither the system keyring nor a browser is accessed.
The file projection follows rmcp-client/src/oauth.rs::FallbackTokenEntry.
"""

import asyncio
import hashlib
import json
import time
from dataclasses import replace

import httpx
import pytest

from corki.config import CorkiSettings
from corki.core import LangGraphRuntime
from corki.http_client import OwnedHTTPClient
from corki.models import ModelCompleted
from corki.protocol.events import TurnCompleted
from corki.tools import ToolRegistry


@pytest.mark.parametrize(
    "mode",
    [
        "fresh",
        "fresh_changed_issuer",
        "expired_changed_issuer",
        "explicit_bearer",
        "absent",
        "metadata_unavailable",
    ],
)
def test_cold_runtime_uses_stored_credentials_without_leaking_refresh(tmp_path, monkeypatch, mode):
    async def scenario():
        server_url = "https://resource.invalid/mcp"
        issuer = "https://issuer.invalid"
        current_issuer = "https://changed.invalid" if "changed_issuer" in mode else issuer
        home = tmp_path / "home"
        home.mkdir()
        configuration = home / "config.toml"
        configuration.write_text(
            'mcp_oauth_credentials_store = "file"\n'
            '[mcp.servers.stored]\ntransport = "http"\n'
            f'url = "{server_url}"\n'
            + ('bearer_token_env_var = "CORKI_PROBE_BEARER"\n' if mode == "explicit_bearer" else "")
        )
        # Native ordinary host key: name + '|' + first 16 SHA256 hex digits.
        payload = json.dumps(
            {"headers": {}, "type": "http", "url": server_url}, separators=(",", ":")
        )
        key = "stored|" + hashlib.sha256(payload.encode()).hexdigest()[:16]
        expires_at = int(time.time() * 1000) + (
            -60_000 if mode == "expired_changed_issuer" else 3_600_000
        )
        credential_file = home / ".credentials.json"
        if mode != "absent":
            credential_file.write_text(
                json.dumps(
                    {
                        key: {
                            "server_name": "stored",
                            "server_url": server_url,
                            "issuer": issuer,
                            "client_id": "synthetic-client",
                            "access_token": "synthetic-access",
                            "refresh_token": "synthetic-refresh-must-stay-local",
                            "expires_at": expires_at,
                            "scopes": ["read"],
                        }
                    }
                )
            )
            credential_file.chmod(0o600)
        saved = credential_file.read_bytes() if credential_file.exists() else None
        requests, model_requests, carriers = [], [], []

        def respond(request):
            requests.append(request)
            assert request.url.host in {"resource.invalid", "issuer.invalid", "changed.invalid"}
            assert b"synthetic-refresh-must-stay-local" not in request.content
            if request.url.host != "resource.invalid":
                assert "authorization" not in request.headers, "MCP access token leaked to AS"
            if request.url.path == "/token":
                raise AssertionError("none of these branches may exchange a refresh token")
            if "/.well-known/" in request.url.path:
                if mode == "metadata_unavailable":
                    return httpx.Response(503)
                if "oauth-protected-resource" in request.url.path:
                    return httpx.Response(
                        200,
                        json={"resource": server_url, "authorization_servers": [current_issuer]},
                    )
                return httpx.Response(
                    200,
                    json={
                        "issuer": current_issuer,
                        "authorization_endpoint": current_issuer + "/authorize",
                        "token_endpoint": current_issuer + "/token",
                    },
                )
            assert str(request.url) == server_url
            if request.method == "GET":
                return httpx.Response(
                    401,
                    headers={
                        "www-authenticate": (
                            'Bearer resource_metadata="/.well-known/oauth-protected-resource/mcp"'
                        )
                    },
                )
            expected_bearer = (
                "Bearer synthetic-explicit"
                if mode == "explicit_bearer"
                else "Bearer synthetic-access"
            )
            if request.headers.get("authorization") != expected_bearer:
                return httpx.Response(401)
            assert mode not in {"expired_changed_issuer", "absent"}, "invalid credential used"
            if request.method == "DELETE":
                return httpx.Response(204)
            packet = json.loads(request.content)
            if "id" not in packet:
                return httpx.Response(202)
            if packet["method"] == "initialize":
                result = {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fixture", "version": "1"},
                }
            else:
                assert packet["method"] == "tools/list"
                result = {"tools": [{"name": "lookup", "inputSchema": {"type": "object"}}]}
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": packet["id"], "result": result}
            )

        class Carrier(httpx.MockTransport):
            def __init__(self):
                super().__init__(respond)
                self.closed = False
                carriers.append(self)

            async def aclose(self):
                self.closed = True
                await super().aclose()

        monkeypatch.setattr(httpx.AsyncClient, "_init_transport", lambda *a, **kw: Carrier())
        monkeypatch.setattr(OwnedHTTPClient, "_init_transport", lambda *a, **kw: Carrier())
        monkeypatch.setenv("NO_PROXY", "*")
        monkeypatch.setenv("CORKI_PROBE_BEARER", "synthetic-explicit")

        def no_browser(*args, **kwargs):
            raise AssertionError("cold startup must not launch a browser")

        monkeypatch.setattr("webbrowser.open", no_browser)

        class Model:
            async def stream(self, request):
                model_requests.append(request)
                yield ModelCompleted(())

            async def aclose(self):
                pass

        settings = replace(
            CorkiSettings.for_directory(tmp_path, config_file=configuration),
            execution_permissions=None,
            tool_search_mode="disabled",
        )
        runtime = LangGraphRuntime.create(
            settings=settings,
            model=Model(),
            registry=ToolRegistry(),
            home_path=home,
            database_path=tmp_path / "session.db",
        )
        try:
            events = [event async for event in runtime.stream("inspect available tools")]
            assert isinstance(events[-1], TurnCompleted)
            assert requests, "fixture never reached real HTTP startup"
            visible = any(tool.name == "mcp__stored::lookup" for tool in model_requests[-1].tools)
            assert visible == (mode in {"fresh", "fresh_changed_issuer", "explicit_bearer"}), (
                "cold Runtime did not consume usable stored credentials"
            )
            metadata_requests = [r for r in requests if "/.well-known/" in r.url.path]
            if mode in {
                "fresh",
                "fresh_changed_issuer",
                "expired_changed_issuer",
                "metadata_unavailable",
            }:
                assert metadata_requests, (
                    "stored credential startup never validated issuer metadata"
                )
            else:
                assert not metadata_requests, "explicit bearer/absent credentials need no discovery"
            assert (credential_file.read_bytes() if credential_file.exists() else None) == saved, (
                "access-only fallback must not erase persisted refresh credentials"
            )
        finally:
            await runtime.aclose()
            assert carriers and all(carrier.closed for carrier in carriers)

    asyncio.run(asyncio.wait_for(scenario(), 10))
