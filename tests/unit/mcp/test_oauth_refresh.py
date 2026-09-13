"""Rotation serialization, durable publication and cancellation ownership."""

import asyncio
import hashlib
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from corki.config import MCPServerSettings
from corki.http_client import OwnedHTTPClient
from corki.mcp.json_rpc import MCPProtocolError
from corki.mcp.oauth_discovery import OAuthDiscovery
from corki.mcp.oauth_file import FileOAuthAuthority
from corki.mcp.oauth_refresh import refresh_oauth
from corki.mcp.oauth_store import SERVICE, OAuthCredentialAuthority
from corki.mcp.runtime_environment import MCPRuntimeContext
from corki.storage.file_lock import open_lock, try_lock, unlock_and_close


def setup(tmp_path, monkeypatch, respond):
    base = "https://issuer.fixture.invalid"
    path = tmp_path / ".credentials.json"
    entry = dict(
        server_name="fixture",
        server_url=base,
        client_id="client",
        access_token="old",
        refresh_token="ROTATING_SECRET",
        issuer=base,
        expires_at=0,
        scopes=["read"],
    )
    path.write_text(json.dumps({"selected": entry}))
    authority = FileOAuthAuthority(tmp_path)
    settings = MCPServerSettings("fixture", "http", url=base)

    async def discovery(*args, **kwargs):
        return OAuthDiscovery({"issuer": base, "token_endpoint": base + "/token"}, "fixture")

    monkeypatch.setattr("corki.mcp.oauth_refresh.discover_oauth", discovery)
    monkeypatch.setattr(
        OwnedHTTPClient, "_init_transport", lambda *a, **kw: httpx.MockTransport(respond)
    )
    monkeypatch.setenv("NO_PROXY", "*")
    return authority, settings, path


@pytest.mark.parametrize("replacement", ["usable", "expired", "deleted"])
def test_cancelled_lock_waiter_rereads_authority_before_refresh(tmp_path, monkeypatch, replacement):
    async def scenario():
        contended = asyncio.Event()
        calls = []

        def respond(request):
            calls.append(request)
            assert request.url == "https://issuer.fixture.invalid/token"
            assert b"refresh_token=SUCCESSOR_SECRET" in request.content
            assert b"ROTATING_SECRET" not in request.content
            assert "authorization" not in request.headers
            return httpx.Response(
                200,
                json={
                    "access_token": "refreshed",
                    "refresh_token": "FINAL_SECRET",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )

        authority, settings, path = setup(tmp_path, monkeypatch, respond)
        previous = await authority.load(settings.name, settings.url)
        identity = json.dumps([settings.name, settings.url]).encode()
        lock_path = tmp_path / "mcp-oauth-locks" / (hashlib.sha256(identity).hexdigest() + ".lock")
        owner = open_lock(lock_path)
        assert try_lock(owner)

        def observed_try_lock(file):
            acquired = try_lock(file)
            if not acquired:
                contended.set()
            return acquired

        monkeypatch.setattr("corki.mcp.oauth_refresh.try_lock", observed_try_lock)
        task = asyncio.create_task(
            refresh_oauth(authority, previous, settings, MCPRuntimeContext())
        )
        try:
            await asyncio.wait_for(contended.wait(), 2)
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0)
            assert not task.done()
            assert calls == []
            if replacement == "deleted":
                path.unlink()
            else:
                saved = json.loads(path.read_text())
                saved["selected"].update(
                    access_token="winner",
                    refresh_token="SUCCESSOR_SECRET",
                    expires_at=int(time.time() * 1000) + 3600000 if replacement == "usable" else 0,
                )
                path.write_text(json.dumps(saved))
            before = path.read_bytes() if path.exists() else None
            unlock_and_close(owner)
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            assert len(calls) == (1 if replacement == "expired" else 0)
            if replacement == "expired":
                saved = json.loads(path.read_text())["selected"]
                assert saved["access_token"] == "refreshed"
                assert saved["refresh_token"] == "FINAL_SECRET"
            else:
                assert (path.read_bytes() if path.exists() else None) == before
            successor = open_lock(lock_path)
            try:
                assert try_lock(successor)
            finally:
                unlock_and_close(successor)
        finally:
            if not owner.closed:
                unlock_and_close(owner)
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(asyncio.wait_for(scenario(), 5))


@pytest.mark.parametrize("mode", ["auto", "keyring"])
def test_keyring_concurrent_refresh_adopts_saved_winner(tmp_path, monkeypatch, mode):
    async def scenario():
        calls = []

        async def respond(request):
            calls.append(request)
            assert request.url.path == "/token"
            assert "authorization" not in request.headers
            return httpx.Response(
                200, json={"access_token": "new", "token_type": "Bearer", "expires_in": 3600}
            )

        _, settings, path = setup(tmp_path, monkeypatch, respond)
        original = path.read_bytes()
        value = json.dumps(json.loads(original)["selected"])

        def get(service, key):
            assert service == SERVICE
            return value

        def save(service, key, updated):
            nonlocal value
            assert service == SERVICE
            value = updated

        backend = SimpleNamespace(priority=1, get_password=get, set_password=save)
        monkeypatch.setattr("keyring.get_keyring", lambda: backend)
        first, second = (OAuthCredentialAuthority(tmp_path, mode) for _ in range(2))
        previous = await first.load(settings.name, settings.url)
        other = await second.load(settings.name, settings.url)
        result = await asyncio.gather(
            refresh_oauth(first, previous, settings, MCPRuntimeContext()),
            refresh_oauth(second, other, settings, MCPRuntimeContext()),
        )
        assert all(token.access_token == "new" for token in result)
        assert len(calls) == 1
        assert json.loads(value)["refresh_token"] == "ROTATING_SECRET"
        assert json.loads(value)["scopes"] == ["read"]
        assert path.read_bytes() == original

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("blocked_at", ["provider", "save"])
def test_concurrent_refresh_adopts_winner_and_cancel_joins_persistence(
    tmp_path, monkeypatch, cancel, blocked_at
):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def respond(request):
            calls.append(request)
            assert request.url.path == "/token"
            assert "authorization" not in request.headers
            if blocked_at == "provider":
                entered.set()
                await release.wait()
            return httpx.Response(
                200, json={"access_token": "new", "token_type": "Bearer", "expires_in": 3600}
            )

        authority, settings, path = setup(tmp_path, monkeypatch, respond)
        save = authority.save

        async def held_save(*args):
            if blocked_at == "save":
                entered.set()
                await release.wait()
            await save(*args)

        monkeypatch.setattr(authority, "save", held_save)
        previous = await authority.load(settings.name, settings.url)
        other = FileOAuthAuthority(tmp_path)
        other_previous = await other.load(settings.name, settings.url)
        first = asyncio.create_task(
            refresh_oauth(authority, previous, settings, MCPRuntimeContext())
        )
        second = None
        try:
            await asyncio.wait_for(entered.wait(), 2)
            second = asyncio.create_task(
                refresh_oauth(other, other_previous, settings, MCPRuntimeContext())
            )
            if cancel:
                first.cancel()
                await asyncio.sleep(0)
                first.cancel()
            await asyncio.sleep(0.05)
            assert not first.done() and not second.done()
            release.set()
            if cancel:
                with pytest.raises(asyncio.CancelledError):
                    await first
            else:
                assert (await first).access_token == "new"
            assert (await second).access_token == "new"
            assert len(calls) == 1
            saved = json.loads(path.read_text())["selected"]
            assert saved["refresh_token"] == "ROTATING_SECRET" and saved["scopes"] == ["read"]
            assert (await authority.load(settings.name, settings.url)).access_token == "new"
        finally:
            release.set()
            await asyncio.gather(first, *([second] if second else []), return_exceptions=True)

    asyncio.run(asyncio.wait_for(scenario(), 5))


@pytest.mark.parametrize(
    "failure",
    [
        "redirect",
        "rejected",
        "transient",
        "malformed",
        "duplicate",
        "save",
        "issuer",
        "deleted",
        "newer",
    ],
)
def test_refresh_failure_does_not_publish_or_follow_redirect(tmp_path, monkeypatch, failure):
    async def scenario():
        calls = []

        def respond(request):
            calls.append(request)
            assert request.url.host == "issuer.fixture.invalid"
            if failure == "redirect":
                return httpx.Response(307, headers={"location": "https://other.invalid/token"})
            if failure in {"rejected", "transient"}:
                return httpx.Response(
                    400 if failure == "rejected" else 503,
                    json={"error": "invalid_grant", "error_description": "ROTATING_SECRET"},
                )
            if failure == "duplicate":
                return httpx.Response(
                    200, content=b'{"access_token":"x","access_token":"y","token_type":"Bearer"}'
                )
            return httpx.Response(
                200,
                json={
                    "access_token": "new",
                    "token_type": "Bearer",
                    "expires_in": True if failure == "malformed" else 3600,
                },
            )

        authority, settings, path = setup(tmp_path, monkeypatch, respond)
        previous = await authority.load(settings.name, settings.url)
        before = path.read_bytes()
        if failure == "save":

            async def save(*args):
                raise OSError("synthetic save failure")

            monkeypatch.setattr(authority, "save", save)
        if failure in {"issuer", "newer"}:
            saved = json.loads(path.read_text())
            saved["selected"].update(
                access_token="winner", expires_at=int(time.time() * 1000) + 3600000
            )
            if failure == "issuer":
                saved["selected"]["issuer"] = "https://other.invalid"
            path.write_text(json.dumps(saved))
            before = path.read_bytes()
        if failure == "deleted":
            path.unlink()
        if failure == "newer":
            token = await refresh_oauth(authority, previous, settings, MCPRuntimeContext())
            assert token.access_token == "winner"
        else:
            with pytest.raises((MCPProtocolError, OSError)) as caught:
                await refresh_oauth(authority, previous, settings, MCPRuntimeContext())
            assert "ROTATING_SECRET" not in str(caught.value)
        assert len(calls) == (0 if failure in {"issuer", "deleted", "newer"} else 1)
        assert not path.exists() if failure == "deleted" else path.read_bytes() == before

    asyncio.run(scenario())
