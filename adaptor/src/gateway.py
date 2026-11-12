"""Async loopback gateway with a synchronous lifecycle for service controllers."""

from __future__ import annotations

import argparse
import asyncio
import errno
import hmac
import json
import os
import socket
import ssl
import sys
import threading
import uuid
from collections.abc import Callable
from contextlib import suppress
from functools import partial
from pathlib import Path

import aiohttp
import truststore
from aiohttp import web

from corki_adaptor.common import MAX_BODY as MAX_BODY
from corki_adaptor.common import CredentialsUnavailable
from corki_adaptor.openai.credentials import (
    CredentialReader,
    Credentials,
    environment_credentials,
    read_credentials,
)
from corki_adaptor.openai.forward import UPSTREAM as UPSTREAM
from corki_adaptor.transport import AiohttpClient

PROVIDER_ROUTES = {"openai": "forward_openai", "qoder": "forward_qoder"}


class Gateway:
    def __init__(
        self,
        auth_file: Path | None,
        key: str,
        *,
        client=None,
        port: int = 0,
        credential_provider: Callable[[], Credentials] | None = None,
        control_token: str | None = None,
        owner: str = "",
        qoder_credentials=None,
        startup_provider="openai",
    ):
        self.instance = uuid.uuid4().hex
        self.control_token = control_token
        self.owner = owner
        self.auth_file = auth_file
        self.key = key
        from corki_adaptor.qoder.credentials import QoderCredentialReader

        self.qoder_credentials = qoder_credentials or QoderCredentialReader()
        if credential_provider is None:
            if auth_file is None:
                raise CredentialsUnavailable("An auth file or credential provider is required.")
            credential_provider = partial(read_credentials, auth_file)
        self.credential_provider = credential_provider
        self._openai_credentials = CredentialReader(credential_provider)
        if startup_provider == "qoder":
            self.qoder_credentials()
        else:
            self._openai_credentials()
        self.ssl_context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self.client = client
        self.owns_client = client is None
        self._loop = None
        self._stop = None
        self._stop_requested = threading.Event()
        self._stopped = threading.Event()
        self._stopped.set()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._socket.bind(("127.0.0.1", port))
            self._socket.listen(128)
            self._socket.setblocking(False)
            self.server_address = self._socket.getsockname()
            self.server_port = self.server_address[1]
        except BaseException:
            self._socket.close()
            raise

    def credentials(self) -> Credentials:
        return self._openai_credentials()

    async def _serve(self):
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        if self._stop_requested.is_set():
            self._stop.set()
        session = None
        runner = None
        try:
            if self.owns_client:
                session = aiohttp.ClientSession(
                    connector=aiohttp.TCPConnector(
                        limit=100,
                        keepalive_timeout=5,
                        ssl=self.ssl_context,
                    ),
                    timeout=aiohttp.ClientTimeout(
                        total=None,
                        connect=120,
                        sock_connect=15,
                        sock_read=120,
                    ),
                    trust_env=True,
                    json_serialize=lambda value: json.dumps(
                        value,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                )
                self.client = AiohttpClient(session)
            # aiohttp's read() rejects >= its limit; the public limit is inclusive.
            app = web.Application(client_max_size=MAX_BODY + 1)
            app.router.add_route("*", "/{path:.*}", self._handle)
            runner = web.AppRunner(
                app,
                access_log=None,
                handler_cancellation=True,
                shutdown_timeout=5,
            )
            await runner.setup()
            await web.SockSite(runner, self._socket).start()
            await self._stop.wait()
        finally:
            if runner is not None:
                await runner.cleanup()
            if session is not None:
                await session.close()
            self._loop = None

    def serve_forever(self):
        self._stopped.clear()
        try:
            asyncio.run(self._serve())
        finally:
            self._stopped.set()

    def shutdown(self):
        self._stop_requested.set()
        loop = self._loop
        if loop is not None and self._stop is not None:
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(self._stop.set)
        self._stopped.wait()

    def server_close(self):
        self.shutdown()
        self._socket.close()

    async def _handle(self, request):
        handler = Handler(self, request)
        try:
            if request.method == "GET":
                handler.do_GET()
            elif request.method == "POST":
                await handler.do_POST()
            elif handler.provider_path() is not None:
                handler.error(405, "请使用 POST 调用转发接口")
            if handler.response is None:
                handler.error(500, "Gateway request failed.")
            return handler.response
        except (ConnectionError, TimeoutError, aiohttp.ClientError):
            handler.abort()
            return handler.response if handler.response is not None else web.Response(status=502)


class Handler:
    def __init__(self, server, request):
        self.server = server
        self.request = request
        self.headers = request.headers
        self.path = request.path_qs
        self.response = None

    async def read_body(self):
        async with asyncio.timeout(30):
            return await self.request.read()

    def error(self, status: int, message: str, *, code: str | None = None):
        error = {"message": message, "type": "codex_gateway_error"}
        if code is not None:
            error["code"] = code
        return self.json_response(status, {"error": error})

    def json_response(self, status, value):
        self.response = web.Response(
            status=status,
            body=json.dumps(value, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Cache-Control": "no-store",
            },
        )
        self.response.force_close()
        return self.response

    async def start_stream(self, content_type):
        self.response = web.StreamResponse(
            status=200,
            headers={"Content-Type": content_type, "Cache-Control": "no-cache"},
        )
        self.response.force_close()
        await self.response.prepare(self.request)

    async def write(self, chunk):
        # Awaiting write carries downstream backpressure to the upstream reader.
        await self.response.write(chunk)

    def abort(self):
        # Never finish a failed SSE stream with a clean chunked EOF.
        if self.request.transport is not None:
            self.request.transport.close()

    def provider_path(self):
        parts = self.request.path.strip("/").split("/")
        if not parts[0] or parts[0] in {"v1", "responses", "chat"}:
            self.error(404, "路径需要加入服务商，例如 /openai/v1/responses")
            return None
        provider = parts[0]
        if provider not in PROVIDER_ROUTES:
            self.error(404, f"暂时不支持的供应商：{provider[:80]}")
            return None
        return provider, "/" + "/".join(parts[1:])

    def do_GET(self):
        if self.path == "/__corki_gateway/health":
            return self.json_response(
                200,
                {
                    "service": "corki-adaptor",
                    "version": 2,
                    "instance": self.server.instance,
                    "owner": self.server.owner,
                    "managed": bool(self.server.control_token),
                    "providers": list(PROVIDER_ROUTES),
                },
            )
        if self.provider_path() is not None:
            self.error(405, "请使用 POST 调用转发接口")

    async def do_POST(self):
        if self.path == "/__corki_gateway/stop":
            supplied = self.headers.get("X-Corki-Control", "")
            token = self.server.control_token
            if not token or not hmac.compare_digest(supplied.encode(), token.encode()):
                return self.error(403, "Gateway control denied.")
            self.json_response(200, {"stopping": True})
            self.server._stop_requested.set()
            self.server._loop.call_later(0.05, self.server._stop.set)
            return
        route = self.provider_path()
        if route is not None:
            provider, endpoint = route
            from corki_adaptor.openai.forward import forward as openai_forward
            from corki_adaptor.qoder.forward import forward as qoder_forward

            await {"openai": openai_forward, "qoder": qoder_forward}[provider](self, endpoint)


def main(argv: list[str] | None = None, *, serve: Callable[[Gateway], None] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a private Codex-login OpenAI protocol gateway"
    )
    auth = parser.add_mutually_exclusive_group()
    auth.add_argument(
        "--auth-file",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json",
        help="Codex auth.json; reread for each request (default: CODEX_HOME/auth.json)",
    )
    auth.add_argument(
        "--access-token-env",
        metavar="NAME",
        help="read the upstream access token from this environment variable instead of auth.json",
    )
    parser.add_argument(
        "--account-id-env",
        default="CODEX_ACCOUNT_ID",
        metavar="NAME",
        help="account ID variable for --access-token-env (default: CODEX_ACCOUNT_ID)",
    )
    parser.add_argument("--control-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--owner", default="", help=argparse.SUPPRESS)
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--auto-port", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--ready-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=17001)
    parser.add_argument("--startup-provider", choices=("openai", "qoder"), default="openai")
    parser.add_argument("--key-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        key = args.key_file.expanduser().read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        parser.error("gateway key file is unavailable")
    if not key:
        parser.error("gateway key must not be empty")
    try:
        control_token = (
            args.control_file.read_text(encoding="utf-8").strip() if args.control_file else None
        )
        provider = None
        if args.access_token_env is not None:
            provider = partial(environment_credentials, args.access_token_env, args.account_id_env)
        options = dict(
            credential_provider=provider,
            control_token=control_token,
            owner=args.owner,
            startup_provider=args.startup_provider,
        )
        auth_file = None if provider else args.auth_file.expanduser()
        try:
            gateway = Gateway(auth_file, key, port=args.port, **options)
        except OSError as error:
            if not args.auto_port or error.errno != errno.EADDRINUSE:
                raise
            # Bind port 0 directly: the kernel chooses and reserves a free port
            # atomically, including when a competing listener wins a startup race.
            gateway = Gateway(auth_file, key, port=0, **options)
    except (CredentialsUnavailable, OSError) as exc:
        print(
            "Gateway credentials or control files are unavailable."
            if isinstance(exc, OSError)
            else str(exc),
            file=sys.stderr,
        )
        return 1
    try:
        if args.ready_file:
            ready = args.ready_file
            temporary = ready.with_name(ready.name + ".tmp")
            try:
                with temporary.open("x", encoding="utf-8") as stream:
                    os.chmod(temporary, 0o600)
                    json.dump(
                        {
                            "port": gateway.server_port,
                            "owner": gateway.owner,
                            "instance": gateway.instance,
                        },
                        stream,
                    )
                os.replace(temporary, ready)
            finally:
                temporary.unlink(missing_ok=True)
        if serve is None:
            gateway.serve_forever()
        else:
            serve(gateway)
    except KeyboardInterrupt:
        pass
    finally:
        gateway.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
