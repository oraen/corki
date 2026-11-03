"""Connection ownership does not import MCP policy or alter host-owned clients."""

import asyncio

import httpx
import pytest

from corki.http_client import OwnedHTTPClient
from corki.models import OpenAICompatibleModel, OpenAIResponsesModel, resolve_capabilities


def test_system_proxy_bypasses_local_services_but_preserves_remote_routing(monkeypatch):
    # Mock the common HTTPX proxy discovery seam: on Windows it also reads the
    # system registry, even when HTTP_PROXY is absent from the process environment.
    monkeypatch.setattr(
        "httpx._client.get_environment_proxies",
        lambda: {"http://": "http://proxy.invalid:7890", "https://": "http://proxy.invalid:7890"},
    )

    async def scenario():
        async with OwnedHTTPClient() as client:
            for host in ("localhost", "127.0.0.1", "[::1]"):
                for scheme in ("http", "https"):
                    assert (
                        client._transport_for_url(httpx.URL(f"{scheme}://{host}:17001"))
                        is client._transport
                    )
            for host in ("remote.invalid", "localhost.remote.invalid", "127.0.0.1.remote.invalid"):
                assert (
                    client._transport_for_url(httpx.URL(f"https://{host}")) is not client._transport
                )

        async with OwnedHTTPClient(proxy="http://explicit.invalid:7890") as client:
            assert (
                client._transport_for_url(httpx.URL("http://127.0.0.1:17001"))
                is not client._transport
            )

        transport = httpx.MockTransport(lambda _: httpx.Response(200))
        async with OwnedHTTPClient(mounts={"all://localhost": transport}) as client:
            assert client._transport_for_url(httpx.URL("http://localhost:17001")) is transport

    asyncio.run(scenario())


@pytest.mark.parametrize("follow", [False, True])
def test_shared_client_preserves_httpx_redirect_choice_without_mcp_origin_policy(follow):
    async def scenario():
        requests = []

        def respond(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(307, headers={"location": "https://other.invalid/final"})
            return httpx.Response(200, text="done")

        async with OwnedHTTPClient(
            transport=httpx.MockTransport(respond), follow_redirects=follow
        ) as client:
            response = await client.post("https://initial.invalid/start", content=b"body")
            assert response.status_code == (200 if follow else 307)
            assert len(requests) == (2 if follow else 1)
            if follow:
                assert requests[-1].url.host == "other.invalid"
                assert requests[-1].content == b"body"

    asyncio.run(scenario())


def test_shared_client_does_not_patch_explicit_transport_or_mount():
    async def scenario():
        transport, mounted = httpx.AsyncHTTPTransport(), httpx.AsyncHTTPTransport()
        backends = [t._pool._network_backend for t in (transport, mounted)]
        async with OwnedHTTPClient(transport=transport, mounts={"https://": mounted}):
            assert transport._pool._network_backend is backends[0]
            assert mounted._pool._network_backend is backends[1]

    asyncio.run(scenario())


@pytest.mark.parametrize("consumer", ["chat", "responses"])
def test_consumer_preserves_borrowed_client_identity_and_lifetime(tmp_path, consumer):
    class HostClient(httpx.AsyncClient):
        def __bool__(self):
            return False  # Ownership depends on None, not custom client truthiness.

    async def scenario():
        async with HostClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200))
        ) as client:
            api_mode = "chat_completions" if consumer == "chat" else "responses"
            model_type = OpenAICompatibleModel if consumer == "chat" else OpenAIResponsesModel
            owner = model_type(
                api_key="fixture",
                base_url="https://fixture.invalid",
                capabilities=resolve_capabilities(
                    base_url="https://fixture.invalid", api_mode=api_mode
                ),
                client=client,
            )
            try:
                assert owner._client is client
            finally:
                await owner.aclose()
            assert not client.is_closed

    asyncio.run(scenario())
