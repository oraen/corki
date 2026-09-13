"""A resolved store must never fall back to stale credentials during refresh."""

import asyncio
import json
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from corki.mcp.oauth_file import FileOAuthToken, _save
from corki.mcp.oauth_store import (
    SERVICE,
    OAuthCredentialAuthority,
    credential_key,
    save_login_credentials,
)


@pytest.mark.parametrize("mode", ["auto", "keyring"])
def test_keyring_read_and_refresh_remain_pinned(tmp_path, monkeypatch, mode):
    async def scenario():
        token = FileOAuthToken(
            "fixture", "https://fixture.invalid/mcp", "client", "new", "issuer", "refresh", None
        )
        key = credential_key(token.server_name, token.server_url)
        entries = {key: json.dumps({**asdict(token), "scopes": ["read"], "future": "keep"})}
        failed = False

        def get(service, name):
            assert service == SERVICE
            if failed:
                raise OSError("backend unavailable")
            return entries.get(name)

        backend = SimpleNamespace(
            priority=1,
            get_password=get,
            set_password=lambda service, name, value: entries.__setitem__(name, value),
        )
        monkeypatch.setattr("keyring.get_keyring", lambda: backend)
        (tmp_path / "mcp-oauth-locks").mkdir()
        _save(tmp_path, None, replace(token, access_token="stale-file"), [])
        original = (tmp_path / ".credentials.json").read_bytes()
        authority = OAuthCredentialAuthority(tmp_path, mode)
        assert await authority.load(token.server_name, token.server_url) == token
        updated = replace(token, access_token="rotated", refresh_token="rotated-refresh")
        await authority.save(token, updated)
        assert await authority.load(token.server_name, token.server_url) == updated
        assert json.loads(entries[key])["future"] == "keep"
        assert json.loads(entries[key])["scopes"] == ["read"]
        failed = True
        with pytest.raises(OSError):
            await authority.load(token.server_name, token.server_url)
        with pytest.raises(OSError):
            await authority.save(updated, token)
        assert (tmp_path / ".credentials.json").read_bytes() == original

    asyncio.run(scenario())


@pytest.mark.parametrize("unavailable", [False, True])
def test_auto_file_resolution_does_not_switch_to_later_keyring(tmp_path, monkeypatch, unavailable):
    async def scenario():
        calls = []

        def get(*args):
            calls.append(args)
            if unavailable:
                raise OSError("unavailable")
            return None

        monkeypatch.setattr(
            "keyring.get_keyring", lambda: SimpleNamespace(priority=1, get_password=get)
        )
        token = FileOAuthToken(
            "fixture", "https://fixture.invalid/mcp", "client", "file", None, None, None
        )
        (tmp_path / "mcp-oauth-locks").mkdir()
        _save(tmp_path, None, token, [])
        authority = OAuthCredentialAuthority(tmp_path, "auto")
        assert await authority.load(token.server_name, token.server_url) == token
        assert await authority.load(token.server_name, token.server_url) == token
        assert len(calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["auto", "keyring"])
@pytest.mark.parametrize("failure", [None, "backend", "cleanup"])
def test_first_login_save_and_exact_file_cleanup(tmp_path, monkeypatch, caplog, mode, failure):
    token = FileOAuthToken(
        "fixture", "https://fixture.invalid/mcp", "client", "new", None, None, None
    )
    other = replace(token, server_name="unrelated", access_token="keep")
    (tmp_path / "mcp-oauth-locks").mkdir()
    _save(tmp_path, None, replace(token, access_token="old"), [])
    _save(tmp_path, None, other, [])
    before = (tmp_path / ".credentials.json").read_bytes()
    writes = {}

    def save(service, key, value):
        assert service == SERVICE
        if failure == "backend":
            raise OSError("SENSITIVE_BACKEND_MESSAGE")
        writes[key] = value

    monkeypatch.setattr(
        "keyring.get_keyring", lambda: SimpleNamespace(priority=1, set_password=save)
    )
    if failure == "cleanup":

        def fail_cleanup(*args, **kwargs):
            assert kwargs == {"remove": True}
            raise OSError("SENSITIVE_CLEANUP_MESSAGE")

        monkeypatch.setattr("corki.mcp.oauth_store._save", fail_cleanup)
    if failure == "backend" and mode == "keyring":
        with pytest.raises(OSError):
            save_login_credentials(tmp_path, token, ["read"], mode)
        assert (tmp_path / ".credentials.json").read_bytes() == before
    else:
        save_login_credentials(tmp_path, token, ["read"], mode)
        entries = list(json.loads((tmp_path / ".credentials.json").read_bytes()).values())
        assert next(e for e in entries if e["server_name"] == "unrelated")["access_token"] == "keep"
        selected = [e for e in entries if e["server_name"] == token.server_name]
        if failure == "backend":
            assert selected[0]["access_token"] == "new"
            assert not writes
        else:
            assert (
                json.loads(writes[credential_key(token.server_name, token.server_url)])[
                    "access_token"
                ]
                == "new"
            )
            assert bool(selected) == (failure == "cleanup")
            if failure == "cleanup":
                assert (tmp_path / ".credentials.json").read_bytes() == before
    assert "SENSITIVE_" not in caplog.text
