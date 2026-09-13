"""Generic public-client login is independent of model credentials and official services."""

import asyncio
import base64
import hashlib
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from corki.config import MCPServerSettings
from corki.config.mcp_oauth import MCPServerOAuthSettings
from corki.mcp.oauth_discovery import OAuthDiscovery
from corki.mcp.oauth_file import _save
from corki.mcp.oauth_login import login_oauth
from corki.mcp.oauth_store import OAuthCredentialAuthority, save_login_credentials
from corki.mcp.runtime_environment import MCPRuntimeContext
from corki.storage.file_lock import try_lock


@pytest.mark.parametrize("issuer_bound", [True, False])
@pytest.mark.parametrize("cancel_at", [None, "lock", "token", "save"])
@pytest.mark.parametrize("store_mode", ["file", "auto", "keyring"])
def test_preconfigured_client_skips_registration_and_persists(
    tmp_path, monkeypatch, issuer_bound, cancel_at, store_mode
):
    async def scenario():
        issuer = "https://auth.fixture.invalid"
        calls, urls, clients = [], [], []
        started, release = asyncio.Event(), asyncio.Event()
        worker_release = threading.Event()
        loop = asyncio.get_running_loop()
        entries = {}
        backend = SimpleNamespace(
            priority=1,
            get_password=lambda service, key: entries.get((service, key)),
            set_password=lambda service, key, value: entries.__setitem__((service, key), value),
        )
        monkeypatch.setattr("keyring.get_keyring", lambda: backend)
        settings = MCPServerSettings(
            "fixture",
            "http",
            url="https://mcp.fixture.invalid/mcp",
            oauth=MCPServerOAuthSettings(client_id="configured-client"),
            scopes=("read",),
        )

        async def handle(request):
            calls.append(request)
            assert str(request.url) == issuer + "/token"
            assert request.method == "POST"
            assert "authorization" not in request.headers
            fields = parse_qs(request.content.decode())
            query = parse_qs(urlsplit(urls[0]).query)
            assert fields["client_id"] == ["configured-client"]
            assert fields["code"] == ["fixture-code"]
            assert fields["redirect_uri"] == query["redirect_uri"]
            assert fields["resource"] == [settings.url]
            digest = hashlib.sha256(fields["code_verifier"][0].encode()).digest()
            assert query["code_challenge"] == [
                base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
            ]
            assert query["scope"] == ["read"]
            if cancel_at == "token":
                started.set()
                await release.wait()
            return httpx.Response(
                200,
                json={
                    "access_token": "fixture-access",
                    "refresh_token": "fixture-refresh",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )

        def client(**kwargs):
            value = httpx.AsyncClient(
                transport=httpx.MockTransport(handle), trust_env=False, **kwargs
            )
            clients.append(value)
            return value

        monkeypatch.setattr("corki.mcp.oauth_login.OwnedHTTPClient", client)

        if cancel_at == "lock":

            def acquire(lock):
                started.set()
                return try_lock(lock) if release.is_set() else False

            monkeypatch.setattr("corki.mcp.oauth_login.try_lock", acquire)

        if cancel_at == "save":

            def save(*args):
                loop.call_soon_threadsafe(started.set)
                assert worker_release.wait(5), "test did not release credential writer"
                return (_save if store_mode == "file" else save_login_credentials)(*args)

            monkeypatch.setattr(
                "corki.mcp.oauth_login._save"
                if store_mode == "file"
                else "corki.mcp.oauth_login.save_login_credentials",
                save,
            )

        async def request(name, params):
            assert name == settings.name
            urls.append(params["url"])
            query = parse_qs(urlsplit(params["url"]).query)
            redirect = urlsplit(query["redirect_uri"][0])
            assert (
                redirect.path == "/callback"
                if issuer_bound
                else redirect.path.startswith("/callback/")
            )
            reader, writer = await asyncio.open_connection(redirect.hostname, redirect.port)
            try:
                target = (
                    redirect.path
                    + "?"
                    + urlencode(
                        {
                            "state": query["state"][0],
                            "code": "fixture-code",
                            **({"iss": issuer} if issuer_bound else {}),
                        }
                    )
                )
                writer.write(f"GET {target} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode())
                await writer.drain()
                assert (await reader.read()).startswith(b"HTTP/1.1 200")
            finally:
                writer.close()
                await writer.wait_closed()
            return {"action": "accept"}

        discovery = OAuthDiscovery(
            {
                **({"issuer": issuer} if issuer_bound else {}),
                "authorization_endpoint": issuer + "/authorize",
                "token_endpoint": issuer + "/token",
                "authorization_response_iss_parameter_supported": issuer_bound,
            },
            "fixture",
        )
        owner = asyncio.create_task(
            login_oauth(
                settings,
                MCPRuntimeContext(),
                tmp_path,
                SimpleNamespace(request=request),
                discovery,
                store_mode=store_mode,
            )
        )
        try:
            if cancel_at is None:
                assert await owner
            else:
                await started.wait()
                owner.cancel()
                await asyncio.sleep(0)
                owner.cancel()
                await asyncio.sleep(0)
                assert not owner.done(), "cancel abandoned the submitted token transaction"
                assert not clients[0].is_closed
                release.set()
                worker_release.set()
                with pytest.raises(asyncio.CancelledError):
                    await owner
        finally:
            release.set()
            worker_release.set()
            if not owner.done():
                owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)
        assert clients and all(client.is_closed for client in clients)
        token = await OAuthCredentialAuthority(tmp_path, store_mode).load(
            settings.name, settings.url
        )
        if cancel_at == "lock":
            assert calls == [], "cancelled lock waiter must not submit a new token exchange"
            assert token is None
            return
        assert len(calls) == 1
        assert token.client_id == "configured-client"
        assert token.access_token == "fixture-access"
        assert token.refresh_token == "fixture-refresh"
        assert token.usable()

    asyncio.run(asyncio.wait_for(scenario(), 5))
