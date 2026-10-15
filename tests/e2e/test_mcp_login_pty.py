"""Real terminal login: Ctrl+C, provider rejection and successful callback."""

import http.client
import os
import socket
import sys
from urllib.parse import parse_qs, urlencode, urlsplit

import pexpect
import pytest

BOOTSTRAP = r"""
import httpx
import webbrowser
from corki.cli.main import main
import corki.cli.mcp_login as login
from corki.config.mcp_requirements import MCPRequirements
from corki.http_client import OwnedHTTPClient

transports = []
def handle(request):
    assert request.url.host in {"mcp.fixture.invalid", "auth.fixture.invalid"}
    assert "authorization" not in request.headers
    if "oauth-protected-resource" in request.url.path:
        return httpx.Response(200, json={"resource": "https://mcp.fixture.invalid/mcp",
            "authorization_servers": ["https://auth.fixture.invalid"]})
    if "/.well-known/" in request.url.path:
        return httpx.Response(200, json={"issuer": "https://auth.fixture.invalid",
            "authorization_endpoint": "https://auth.fixture.invalid/authorize",
            "token_endpoint": "https://auth.fixture.invalid/token",
            "authorization_response_iss_parameter_supported": True})
    if request.url.path == "/mcp":
        return httpx.Response(401)
    assert request.url.path == "/token"
    print("FIXTURE_TOKEN_POST", flush=True)
    return httpx.Response(200, json={"access_token": "fixture-secret",
        "token_type": "Bearer", "expires_in": 3600})
class Transport(httpx.MockTransport):
    closed = False
    def __init__(self):
        super().__init__(handle)
        transports.append(self)
    async def aclose(self):
        self.closed = True
        await super().aclose()
OwnedHTTPClient._init_transport = lambda *args, **kwargs: Transport()
httpx.AsyncClient._init_transport = lambda *args, **kwargs: Transport()
login.load_mcp_requirements = MCPRequirements
webbrowser.open = lambda url: False
code = main(["mcp", "login", "fixture"])
assert transports and all(t.closed for t in transports)
print("FIXTURE_CLOSED", flush=True)
raise SystemExit(code)
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("action", ["cancel", "reject", "accept"])
def test_real_terminal_mcp_login(tmp_path, width, action):
    home = tmp_path / "home"
    home.mkdir()
    config = home / "config.toml"
    config.write_text(
        'mcp_oauth_credentials_store = "file"\n'
        '[mcp.servers.fixture]\ntransport = "http"\nurl = "https://mcp.fixture.invalid/mcp"\n'
        '[mcp.servers.fixture.oauth]\nclient_id = "fixture-client"\n'
    )
    original = config.read_bytes()
    child = pexpect.spawn(
        sys.executable,
        ["-c", BOOTSTRAP],
        cwd=str(tmp_path),
        env={**os.environ, "CORKI_HOME": str(home), "NO_PROXY": "*"},
        dimensions=(24, width),
        encoding="utf-8",
        timeout=10,
    )
    transcript = ""
    try:
        child.expect(r"https://auth\.fixture\.invalid/authorize[^\r\n]+")
        transcript += child.before + child.after
        query = parse_qs(urlsplit(child.after).query)
        redirect = urlsplit(query["redirect_uri"][0])
        child.expect("copy the URL above manually.")
        transcript += child.before + child.after
        if action == "cancel":
            child.sendcontrol("c")
        else:
            connection = http.client.HTTPConnection(redirect.hostname, redirect.port, timeout=5)
            try:
                fields = {"state": query["state"][0], "iss": "https://auth.fixture.invalid"}
                fields.update(
                    {"code": "fixture-code"} if action == "accept" else {"error": "access_denied"}
                )
                connection.request("GET", redirect.path + "?" + urlencode(fields))
                response = connection.getresponse()
                response.read()
            finally:
                connection.close()
        child.expect(pexpect.EOF)
        transcript += child.before
        child.close()
        assert child.exitstatus == {"cancel": 130, "reject": 1, "accept": 0}[action], transcript
        assert "FIXTURE_CLOSED" in transcript
        assert ("Successfully logged in" in transcript) == (action == "accept")
        assert ("FIXTURE_TOKEN_POST" in transcript) == (action == "accept")
        assert "fixture-secret" not in transcript
        assert "Traceback" not in transcript
        assert (home / ".credentials.json").exists() == (action == "accept")
        assert not (home / "history").exists()
        assert not (home / "sessions").exists()
        assert config.read_bytes() == original
        with pytest.raises(OSError):
            socket.create_connection((redirect.hostname, redirect.port), timeout=1)
    finally:
        if child.isalive():
            child.close(force=True)
