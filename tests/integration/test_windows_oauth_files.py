"""Credential publication keeps a private ACL before and after atomic replacement."""

import os
from dataclasses import replace

import pytest

from corki.mcp.oauth_file import FileOAuthToken, _read, _save

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires native Windows ACLs")


def test_oauth_login_and_refresh_publish_private_files(tmp_path, monkeypatch):
    import json
    import msvcrt

    from corki.platforms.windows_acl import WindowsPrivateACL

    home = tmp_path / "凭据 空间"
    home.mkdir()
    security = WindowsPrivateACL()
    original = json.dump
    inspected = []

    def checked_dump(value, stream, **kwargs):
        assert stream.tell() == 0
        security.verify(msvcrt.get_osfhandle(stream.fileno()))
        inspected.append(True)
        return original(value, stream, **kwargs)

    monkeypatch.setattr(json, "dump", checked_dump)
    token = FileOAuthToken(
        "fixture",
        "https://example.invalid/mcp",
        "client",
        "fixture-only",
        None,
        "refresh-fixture",
        None,
    )
    assert _read(home, token.server_name, token.server_url) is None
    _save(home, None, token, [])
    updated = replace(token, access_token="updated-fixture")
    _save(home, token, updated, [])
    assert _read(home, token.server_name, token.server_url) == updated
    with (home / ".credentials.json").open("rb") as stream:
        security.verify(msvcrt.get_osfhandle(stream.fileno()))
    assert len(inspected) == 2
    assert not list(home.glob(".mcp-credentials-*"))
    (home / ".credentials.json").unlink()
