"""Actual CLI entry, isolated provider HTTP and a real loopback authorization callback."""

import http.client
import importlib
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from corki.cli.main import build_parser, main
from corki.config.mcp_requirements import MCPRequirements
from corki.http_client import OwnedHTTPClient


@pytest.mark.parametrize(
    "mode",
    [
        "success",
        "provider_error",
        "save_error",
        "unknown",
        "disabled",
        "managed",
        "auto",
        "keyring",
        "stdio",
    ],
)
def test_mcp_login_cli_without_model_session(tmp_path, monkeypatch, capsys, mode):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CORKI_HOME", str(home))
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setattr(
        "corki.cli.mcp_login.load_mcp_requirements",
        lambda: MCPRequirements(servers=()) if mode == "managed" else MCPRequirements(),
    )
    config = home / "config.toml"
    transport = (
        'transport = "stdio"\ncommand = "never-execute"'
        if mode == "stdio"
        else 'transport = "http"\nurl = "https://mcp.fixture.invalid/mcp"'
    )
    config.write_text(
        f'mcp_oauth_credentials_store = "{mode if mode in {"auto", "keyring"} else "file"}"\n'
        f"[mcp.servers.fixture]\n{transport}\n"
        f"enabled = {'false' if mode == 'disabled' else 'true'}\n"
        'scopes = ["configured"]\n[mcp.servers.fixture.oauth]\nclient_id = "fixture-client"\n'
    )
    initial = config.read_bytes()
    requests, browsers, transports = [], [], []
    saved = {}
    backend = SimpleNamespace(
        priority=1 if mode == "keyring" else -1,
        set_password=lambda service, key, value: saved.__setitem__((service, key), value),
    )
    monkeypatch.setattr("keyring.get_keyring", lambda: backend)

    def handle(request):
        requests.append(request)
        assert request.url.host in {"mcp.fixture.invalid", "auth.fixture.invalid"}
        assert "authorization" not in request.headers
        if "oauth-protected-resource" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "resource": "https://mcp.fixture.invalid/mcp",
                    "authorization_servers": ["https://auth.fixture.invalid"],
                },
            )
        if "/.well-known/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "issuer": "https://auth.fixture.invalid",
                    "authorization_endpoint": "https://auth.fixture.invalid/authorize",
                    "token_endpoint": "https://auth.fixture.invalid/token",
                    "authorization_response_iss_parameter_supported": True,
                },
            )
        if request.url.path == "/mcp":
            return httpx.Response(401)
        assert request.url.path == "/token"
        assert parse_qs(request.content.decode())["code"] == ["fixture-code"]
        return httpx.Response(
            400 if mode == "provider_error" else 200,
            json={
                "access_token": "secret-not-for-terminal",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        )

    class Transport(httpx.MockTransport):
        closed = False

        def __init__(self):
            super().__init__(handle)
            transports.append(self)

        async def aclose(self):
            self.closed = True
            await super().aclose()

    monkeypatch.setattr(OwnedHTTPClient, "_init_transport", lambda *args, **kwargs: Transport())
    monkeypatch.setattr(httpx.AsyncClient, "_init_transport", lambda *args, **kwargs: Transport())

    def browser(url):
        browsers.append(url)
        query = parse_qs(urlsplit(url).query)
        assert query["scope"] == ["read write"]
        redirect = urlsplit(query["redirect_uri"][0])
        assert redirect.hostname == "127.0.0.1"
        connection = http.client.HTTPConnection(redirect.hostname, redirect.port, timeout=5)
        try:
            target = (
                redirect.path
                + "?"
                + urlencode(
                    {
                        "state": query["state"][0],
                        "iss": "https://auth.fixture.invalid",
                        "code": "fixture-code",
                    }
                )
            )
            connection.request("GET", target)
            response = connection.getresponse()
            assert response.status == 200
            response.read()
        finally:
            connection.close()
        return False  # Same fallback message as a failed automatic browser launch.

    monkeypatch.setattr("webbrowser.open", browser)
    if mode == "save_error":

        def fail_save(*args):
            raise ValueError("secret-not-for-terminal")

        monkeypatch.setattr("corki.mcp.oauth_login._save", fail_save)

    def no_model(**kwargs):
        raise AssertionError("MCP login must not construct a model application")

    monkeypatch.setattr(importlib.import_module("corki.cli.main"), "build_application", no_model)
    code = main(
        ["mcp", "login", "unknown" if mode == "unknown" else "fixture", "--scopes", "read,write"]
    )
    output = capsys.readouterr()
    assert code == (0 if mode in {"success", "auto", "keyring"} else 1)
    assert ("Successfully logged in" in output.out) == (mode in {"success", "auto", "keyring"})
    assert "secret-not-for-terminal" not in output.out + output.err
    assert (home / ".credentials.json").exists() == (mode in {"success", "auto"})
    if mode == "keyring":
        assert len(saved) == 1
        assert next(iter(saved))[0] == "Corki MCP Credentials"
        assert json.loads(next(iter(saved.values())))["access_token"] == "secret-not-for-terminal"
    assert config.read_bytes() == initial
    assert not (home / "sessions").exists()
    assert not (home / "history").exists()
    if mode in {"success", "auto", "keyring", "provider_error", "save_error"}:
        assert len(browsers) == 1
        assert "copy the URL" in output.err
        assert sum(request.url.path == "/token" for request in requests) == 1
    else:
        assert not requests and not browsers
    assert all(transport.closed for transport in transports)


def test_login_parser_and_resume_overrides():
    parsed = build_parser().parse_args(["mcp", "login", "docs", "--scopes", "read,write"])
    assert (parsed.command, parsed.mcp_command, parsed.name, parsed.scopes) == (
        "mcp",
        "login",
        "docs",
        "read,write",
    )
    parsed = build_parser().parse_args(["resume", "thread", "--model", "custom"])
    assert parsed.thread_id == "thread" and parsed.model == "custom"


def test_cli_login_interrupt_is_not_success(monkeypatch, capsys):
    async def interrupted(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr("corki.cli.mcp_login.run_login", interrupted)
    assert main(["mcp", "login", "fixture"]) == 130
    output = capsys.readouterr()
    assert "cancelled" in output.err
    assert "Successfully" not in output.out
