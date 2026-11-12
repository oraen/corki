"""Real sockets exercise aiohttp pooling, incremental SSE and disconnect cleanup."""

import asyncio
import io
import json
import ssl
import threading
from contextlib import asynccontextmanager

import aiohttp
import pytest
from aiohttp import web

from corki_adaptor.common import MAX_BODY
from corki_adaptor.gateway import Gateway
from corki_adaptor.openai.credentials import Credentials


@asynccontextmanager
async def running(monkeypatch, upstream):
    app = web.Application(client_max_size=MAX_BODY + 4096)
    app.router.add_post("/responses", upstream)
    runner = web.AppRunner(app, access_log=None, handler_cancellation=True)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(
        "corki_adaptor.openai.forward.UPSTREAM", f"http://127.0.0.1:{port}/responses"
    )
    server = Gateway(
        None,
        "test-key",
        credential_provider=lambda: Credentials("fake-token", "fake-account"),
    )
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        async with aiohttp.ClientSession(
            base_url=f"http://127.0.0.1:{server.server_port}",
            trust_env=False,
            headers={"Authorization": "Bearer test-key"},
            timeout=aiohttp.ClientTimeout(total=5),
        ) as client:
            yield server, client
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(5)
        await runner.cleanup()
        assert not thread.is_alive()


def completed(text="ok"):
    return (
        "data: "
        + json.dumps(
            {
                "type": "response.completed",
                "response": {
                    "id": "r",
                    "status": "completed",
                    "output": [
                        {"type": "message", "content": [{"type": "output_text", "text": text}]}
                    ],
                },
            },
            ensure_ascii=False,
        )
        + "\n\n"
    )


def test_live_sse_first_chunk_before_completion_and_health_not_blocked(monkeypatch):
    async def scenario():
        release = asyncio.Event()

        async def upstream(request):
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(b'data: {"type":"response.output_text.delta","delta":"hi"}\n\n')
            await release.wait()
            await response.write(completed("hi").encode())
            return response

        async with (
            running(monkeypatch, upstream) as (_, client),
            client.post("/openai/responses", json={"model": "fake", "stream": True}) as r,
        ):
            assert b"delta" in await r.content.readany()
            assert not release.is_set()
            # Another request must progress while the upstream is suspended.
            async with client.get("/__corki_gateway/health") as health:
                assert health.status == 200
            release.set()
            assert b"response.completed" in await r.read()

    asyncio.run(scenario())


def test_disconnect_cancels_upstream_and_returns_pool_slot(monkeypatch):
    async def scenario():
        cancelled = asyncio.Event()
        connected = asyncio.Event()

        async def upstream(request):
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(b"data: first\n\n")
            connected.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async with running(monkeypatch, upstream) as (server, client):
            r = await client.post("/openai/responses", json={"model": "fake", "stream": True})
            await r.content.readany()
            await connected.wait()
            r.close()
            await asyncio.wait_for(cancelled.wait(), 2)

            async def acquired_count():
                return len(server.client.session.connector._acquired)

            count = await asyncio.wrap_future(
                asyncio.run_coroutine_threadsafe(acquired_count(), server._loop)
            )
            assert count == 0

    asyncio.run(scenario())


def test_no_upstream_redirect_following(monkeypatch):
    async def scenario():
        async def upstream(request):
            return web.Response(status=302, headers={"Location": "http://127.0.0.1:1/private"})

        async with (
            running(monkeypatch, upstream) as (_, client),
            client.post("/openai/responses", json={"model": "fake"}) as response,
        ):
            assert response.status == 502
            assert "302" in (await response.json())["error"]["message"]

    asyncio.run(scenario())


@pytest.mark.filterwarnings("ignore:Sending a large body directly:ResourceWarning")
def test_request_body_limit_is_inclusive(monkeypatch):
    async def scenario():
        calls = []

        async def upstream(request):
            payload = await request.json()
            calls.append(len(payload["input"]))
            return web.Response(text=completed(), content_type="text/event-stream")

        async with running(monkeypatch, upstream) as (_, client):
            prefix = b'{"model":"fake","input":"'
            suffix = b'"}'
            body = prefix + b"x" * (MAX_BODY - len(prefix) - len(suffix)) + suffix
            async with client.post("/openai/responses", data=io.BytesIO(body)) as response:
                assert response.status == 200
                assert (await response.json())["id"] == "r"
            async with client.post("/openai/responses", data=io.BytesIO(body + b" ")) as response:
                assert response.status == 413
                assert (await response.json())["error"]["type"] == "codex_gateway_error"
            assert len(calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ["\r", "\r\n", "\n"])
def test_sse_line_boundaries_across_network_chunks(monkeypatch, boundary):
    async def scenario():
        data = completed().replace("\n", boundary).encode()

        async def upstream(request):
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            for value in data:
                await response.write(bytes([value]))
                await asyncio.sleep(0)
            return response

        async with (
            running(monkeypatch, upstream) as (_, client),
            client.post("/openai/responses", json={"model": "fake"}) as response,
        ):
            assert response.status == 200
            assert (await response.json())["id"] == "r"

    asyncio.run(scenario())


@pytest.mark.parametrize("stream", [True, False])
def test_large_utf8_event_is_not_limited_to_aiohttp_readline_buffer(monkeypatch, stream):
    async def scenario():
        text = "中\u2028文" * 30000
        data = completed(text).encode("utf-8")

        async def upstream(request):
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            # Split multibyte UTF-8 at arbitrary byte offsets, including >64 KiB events.
            for position in range(0, len(data), 10001):
                await response.write(data[position : position + 10001])
                await asyncio.sleep(0)
            return response

        async with (
            running(monkeypatch, upstream) as (_, client),
            client.post(
                "/openai/chat/completions",
                json={
                    "model": "fake",
                    "stream": stream,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            ) as response,
        ):
            assert response.status == 200
            if stream:
                body = await response.read()
                packets = [
                    json.loads(line[6:])
                    for line in body.split(b"\n")
                    if line.startswith(b"data: ") and line != b"data: [DONE]"
                ]
                assert packets[0]["choices"][0]["delta"]["content"] == text
                assert body.endswith(b"data: [DONE]\n\n")
            else:
                assert (await response.json())["choices"][0]["message"]["content"] == text

    asyncio.run(scenario())


def test_aiohttp_transport_rejects_self_signed_tls(monkeypatch, tmp_path):
    """The production client must verify both the certificate chain and hostname."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    async def scenario():
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        app = web.Application()

        async def upstream(request):
            return web.Response(text=completed())

        app.router.add_post("/responses", upstream)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=context)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with running(monkeypatch, upstream) as (_, client):
                monkeypatch.setattr(
                    "corki_adaptor.openai.forward.UPSTREAM", f"https://127.0.0.1:{port}/responses"
                )
                async with client.post("/openai/responses", json={"model": "fake"}) as response:
                    assert response.status == 424
                    body = await response.json()
                    assert "certificate verification failed" in body["error"]["message"]
                    assert "code" not in body["error"]
        finally:
            await runner.cleanup()

    asyncio.run(scenario())
