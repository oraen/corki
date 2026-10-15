"""OAuth discovery authority, wire semantics and owned cancellation."""

import asyncio

import httpx
import pytest

from corki.config import MCPServerSettings
from corki.mcp.oauth_discovery import discover_oauth
from corki.mcp.oauth_metadata import (
    OAuthDiscoveryError,
    allowed_authorization_server,
    challenge_resource,
    issuers_match,
    metadata_document,
    resource_matches,
)
from corki.mcp.runtime_environment import MCPHTTPEnvironment, MCPRuntimeContext


@pytest.mark.parametrize(
    "scopes,expected_scopes",
    [
        (None, None),
        ([], None),
        (["", " \u2003\t"], None),
        (
            ["profile", " email ", "profile", "\u2003read\u2003", "EMAIL"],
            ("profile", "email", "read", "EMAIL"),
        ),
        (["\x1cread\x1c"], ("\x1cread\x1c",)),
    ],
)
@pytest.mark.parametrize(
    "issuer_support,include_issuer,expected_mode",
    [
        (True, True, "issuer_bound"),
        (True, False, "callback_specific"),
        (False, True, "callback_specific"),
        ("true", True, "callback_specific"),
        (1, True, "callback_specific"),
        (None, True, "callback_specific"),
    ],
)
def test_discovery_projects_login_contract(
    scopes, expected_scopes, issuer_support, include_issuer, expected_mode
):
    """Native auth_status normalization and callback-mode fallback, over real HTTP parsing."""

    async def scenario():
        metadata = {
            "authorization_endpoint": "https://as.invalid/authorize",
            "token_endpoint": "https://as.invalid/token",
            "scopes_supported": scopes,
            "authorization_response_iss_parameter_supported": issuer_support,
        }
        if include_issuer:
            metadata["issuer"] = "https://as.invalid"

        def respond(request):
            if request.url.path == "/.well-known/oauth-authorization-server":
                return httpx.Response(200, json=metadata)
            return httpx.Response(404)

        async with httpx.MockTransport(respond) as transport:
            settings = MCPServerSettings(
                "fixture", "http", url="https://as.invalid/mcp", environment_id="remote"
            )
            result = await discover_oauth(
                settings, MCPRuntimeContext((MCPHTTPEnvironment("remote", transport),))
            )
            assert result is not None
            assert result.scopes_supported == expected_scopes
            assert result.callback_mode == expected_mode
            # Discovery normalization must not overwrite the SDK's raw metadata:
            # registration/session scope selection is a separate operation.
            assert result.metadata["scopes_supported"] == scopes

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "resource,valid",
    [
        ("https://mcp.invalid/mcp", True),
        ("https://mcp.invalid/mcp?other", True),
        ("https://mcp.invalid/", True),
        ("https://mcp.invalid/m", False),
        ("https://mcp.invalid/mcp#fragment", False),
        ("https://other.invalid/mcp", False),
        ("http://mcp.invalid/mcp", False),
        ("https://mcp.invalid:0/mcp", False),
    ],
)
def test_protected_resource_identity(resource, valid):
    assert resource_matches("https://mcp.invalid/mcp?transport", resource) == valid


@pytest.mark.parametrize(
    "candidate,allowed",
    [
        ("https://as.invalid/tenant", True),
        ("http://as.invalid/tenant", True),
        ("https://metadata.google.internal", False),
        ("https://127.0.0.1", False),
        ("https://10.1.2.3", False),
        ("https://100.64.0.1", False),
        ("https://198.18.0.1", False),
        ("https://192.0.2.1", True),
    ],
)
def test_authorization_server_host_admission(candidate, allowed):
    assert allowed_authorization_server("https://mcp.invalid/mcp", candidate) == allowed


def test_local_metadata_exception_and_root_slash_are_not_blanket_relaxations():
    assert allowed_authorization_server("http://localhost/mcp", "http://127.0.0.1:1234")
    assert not allowed_authorization_server("http://localhost/mcp", "http://10.0.0.1")
    assert issuers_match("https://as.invalid/", "https://as.invalid")
    assert not issuers_match("https://as.invalid/tenant/", "https://as.invalid/tenant")


def test_typed_metadata_duplicates_and_optional_fields():
    assert metadata_document(
        b'{"authorization_endpoint":"a","token_endpoint":"t","issuer":null}', authorization=True
    )
    assert (
        metadata_document(
            b'{"authorization_endpoint":"a","token_endpoint":"t","issuer":1}', authorization=True
        )
        is None
    )
    assert (
        metadata_document(
            b'{"authorization_endpoint":"a","token_endpoint":"t","issuer":"a","issuer":"b"}',
            authorization=True,
        )
        is None
    )
    assert metadata_document(b'{"resource":"a","scopes_supported":null}', authorization=False)
    assert (
        metadata_document(b'{"resource":"a","scopes_supported":[1]}', authorization=False) is None
    )


def test_challenge_tries_later_pointer_but_never_crosses_resource_origin():
    header = (
        'Bearer resource_metadata="https://other.invalid/secret", '
        'resource_metadata=" /metadata ", scope="read"'
    )
    assert challenge_resource(header, "https://mcp.invalid/mcp") == "https://mcp.invalid/metadata"
    assert (
        challenge_resource('Bearer resource_metadata="unterminated', "https://mcp.invalid") is None
    )


def test_discovery_borrows_remote_authority_without_leaking_resource_headers():
    async def scenario():
        requests = []

        def respond(request):
            requests.append(request)
            if request.url.host == "mcp.invalid":
                if request.url.path == "/mcp":
                    return httpx.Response(
                        401, headers={"www-authenticate": 'Bearer resource_metadata="/metadata"'}
                    )
                return httpx.Response(
                    200,
                    json={
                        "resource": "https://mcp.invalid/mcp",
                        "authorization_servers": ["https://as.invalid"],
                    },
                )
            assert request.url.host == "as.invalid"
            return httpx.Response(
                200,
                json={
                    "issuer": "https://as.invalid",
                    "authorization_endpoint": "https://as.invalid/a",
                    "token_endpoint": "https://as.invalid/t",
                },
            )

        class Transport(httpx.MockTransport):
            closed = False

            async def aclose(self):
                self.closed = True

        transport = Transport(respond)
        environment = MCPHTTPEnvironment("remote", transport)
        settings = MCPServerSettings(
            "fixture",
            "http",
            url="https://mcp.invalid/mcp",
            environment_id="remote",
            http_headers=(("x-resource-secret", "fixture-only"),),
        )
        try:
            result = await discover_oauth(settings, MCPRuntimeContext((environment,)))
            assert result.source == "protected_resource"
            assert result.resource == "https://mcp.invalid/mcp"
            assert len(requests) == 3
            assert all(r.extensions["corki_executor_timeout_ms"] == 30000 for r in requests)
            assert all(
                ("x-resource-secret" in r.headers) == (r.url.host == "mcp.invalid")
                for r in requests
            )
            assert not transport.closed, "borrowers must not close the host's carrier"
        finally:
            await transport.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [408, 425, 429, 500, 599])
def test_transient_discovery_error_is_not_unsupported_or_retried(status):
    async def scenario():
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(status)

        transport = httpx.MockTransport(respond)
        environment = MCPHTTPEnvironment("remote", transport)
        settings = MCPServerSettings(
            "fixture", "http", url="https://mcp.invalid", environment_id="remote"
        )
        try:
            with pytest.raises(OAuthDiscoveryError, match="transient"):
                await discover_oauth(settings, MCPRuntimeContext((environment,)))
            assert len(requests) == 1
        finally:
            await transport.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("location,count", [("/again", 10), ("https://other.invalid", 1)])
def test_redirects_stop_before_cross_origin_or_eleventh_request(location, count):
    async def scenario():
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(302, headers={"location": location})

        transport = httpx.MockTransport(respond)
        context = MCPRuntimeContext((MCPHTTPEnvironment("remote", transport),))
        settings = MCPServerSettings(
            "fixture", "http", url="https://mcp.invalid", environment_id="remote"
        )
        try:
            with pytest.raises(OAuthDiscoveryError, match="redirect"):
                await discover_oauth(settings, context)
            assert len(requests) == count
        finally:
            await transport.aclose()

    asyncio.run(scenario())


def test_repeated_cancel_joins_response_close_and_preserves_carrier():
    async def scenario():
        entered, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                entered.set()
                await asyncio.Future()
                yield b""

            async def aclose(self):
                closing.set()
                await release.wait()

        class Transport(httpx.AsyncBaseTransport):
            closed = False

            async def handle_async_request(self, request):
                return httpx.Response(200, stream=Stream())

            async def aclose(self):
                self.closed = True

        transport = Transport()
        context = MCPRuntimeContext((MCPHTTPEnvironment("remote", transport),))
        settings = MCPServerSettings(
            "fixture", "http", url="https://mcp.invalid", environment_id="remote"
        )
        task = asyncio.create_task(discover_oauth(settings, context))
        try:
            await entered.wait()
            task.cancel()
            await closing.wait()
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not transport.closed
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await transport.aclose()

    asyncio.run(asyncio.wait_for(scenario(), 5))


def test_body_limit_releases_stream_and_never_probes_another_url():
    async def scenario():
        from corki.mcp.oauth_discovery import MAX_RESPONSE_BYTES

        class Stream(httpx.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                yield b"x" * MAX_RESPONSE_BYTES
                yield b"x"

            async def aclose(self):
                self.closed = True

        body, requests = Stream(), []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, stream=body)

        transport = httpx.MockTransport(respond)
        context = MCPRuntimeContext((MCPHTTPEnvironment("remote", transport),))
        settings = MCPServerSettings(
            "fixture", "http", url="https://mcp.invalid", environment_id="remote"
        )
        try:
            with pytest.raises(OAuthDiscoveryError, match="1 MiB"):
                await discover_oauth(settings, context)
            assert len(requests) == 1
            assert body.closed
        finally:
            await transport.aclose()

    asyncio.run(scenario())


def test_bearer_and_stdio_skip_before_resolving_environment_or_secrets():
    class NoEnvironment:
        def resolve(self, settings):
            raise AssertionError("a skipped server must not resolve any HTTP authority")

    async def scenario():
        for server in (
            MCPServerSettings("fixture", "stdio", command="fixture"),
            MCPServerSettings(
                "fixture",
                "http",
                url="https://mcp.invalid",
                bearer_token_env_var="ABSENT_FIXTURE_SECRET",
            ),
        ):
            assert await discover_oauth(server, NoEnvironment()) is None

    asyncio.run(scenario())


@pytest.mark.parametrize("header", ["location", "www-authenticate"])
def test_non_ascii_redirect_and_challenge_headers_follow_native_to_str_rules(header):
    async def scenario():
        requests = []

        def respond(request):
            requests.append(request)
            if len(requests) > 1:
                return httpx.Response(404)
            if header == "location":
                return httpx.Response(302, headers=[(b"location", b"/caf\xe9")])
            return httpx.Response(
                401,
                headers=[
                    (b"www-authenticate", b'Bearer resource_metadata="/pointer", realm="caf\xe9"')
                ],
            )

        transport = httpx.MockTransport(respond)
        context = MCPRuntimeContext((MCPHTTPEnvironment("remote", transport),))
        settings = MCPServerSettings(
            "fixture", "http", url="https://mcp.invalid/mcp", environment_id="remote"
        )
        try:
            if header == "location":
                with pytest.raises(UnicodeError):
                    await discover_oauth(settings, context)
                assert len(requests) == 1
            else:
                assert await discover_oauth(settings, context) is None
                assert not any(r.url.path == "/pointer" for r in requests)
        finally:
            await transport.aclose()

    asyncio.run(scenario())
