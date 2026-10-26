"""Opt-in, loopback-only Responses bridge for an existing Codex ChatGPT login.

Codex owns login and refresh. This bridge never writes or refreshes auth.json.
The downstream key is local-only and is not an OpenAI Platform API key.
"""

from __future__ import annotations

import argparse
import errno
import hmac
import json
import os
import ssl
import sys
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import truststore

from corki_openai_gateway.chat import chat_completion, chat_events, completed_response, to_responses

UPSTREAM = "https://chatgpt.com/backend-api/codex/responses"
MAX_BODY = 16 * 1024 * 1024
# Add a provider adapter here to opt it into routing; names never become URLs.
PROVIDER_ROUTES = {"openai": "forward_openai", "qoder": "forward_qoder"}


class CredentialsUnavailable(Exception):
    """Safe error with no credential material attached."""


@dataclass(frozen=True, repr=False)
class Credentials:
    access_token: str
    account_id: str

    def __post_init__(self):
        if any(
            not isinstance(value, str) or not value.strip() or "\r" in value or "\n" in value
            for value in (self.access_token, self.account_id)
        ):
            raise CredentialsUnavailable("A valid access token and account ID are required.")


def environment_credentials(token_env: str, account_env: str) -> Credentials:
    """Read only explicitly selected variables; never fall back to another account."""
    return Credentials(os.environ.get(token_env, ""), os.environ.get(account_env, ""))


def read_credentials(path: Path) -> Credentials:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        tokens = document.get("tokens")
        if document.get("auth_mode") not in (None, "chatgpt") or not isinstance(tokens, dict):
            raise ValueError
        values = [tokens.get("access_token"), tokens.get("account_id")]
        if any(not isinstance(v, str) or not v or "\r" in v or "\n" in v for v in values):
            raise ValueError
        return Credentials(*values)
    except (OSError, ValueError, TypeError, AttributeError):
        raise CredentialsUnavailable(
            "Codex ChatGPT file credentials are unavailable. Sign in with Codex first."
        ) from None


class Gateway(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(
        self,
        auth_file: Path | None,
        key: str,
        *,
        client: httpx.Client | None = None,
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
        from corki_openai_gateway.qoder_credentials import QoderCredentialReader

        self.qoder_credentials = qoder_credentials or QoderCredentialReader()
        if credential_provider is None:
            if auth_file is None:
                raise CredentialsUnavailable("An auth file or credential provider is required.")
            credential_provider = partial(read_credentials, auth_file)
        self.credential_provider = credential_provider
        self.account_id = None
        if startup_provider == "qoder":
            self.qoder_credentials()
        else:
            self.account_id = credential_provider().account_id
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(120, connect=15),
            follow_redirects=False,
            # Match native Codex's OS trust: enterprise roots installed in the
            # system keychain must work without disabling peer/hostname checks.
            verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
        )
        self.owns_client = client is None
        try:
            super().__init__(("127.0.0.1", port), Handler)
        except BaseException:
            if self.owns_client:
                self.client.close()
            raise

    def credentials(self) -> Credentials:
        credentials = self.credential_provider()
        if self.account_id is None:
            self.account_id = credentials.account_id
        elif credentials.account_id != self.account_id:
            raise CredentialsUnavailable("Codex account changed. Restart the gateway to use it.")
        return credentials

    def server_close(self):
        super().server_close()
        if self.owns_client:
            self.client.close()


class Handler(BaseHTTPRequestHandler):
    server: Gateway
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, format, *args):
        # Neither prompts, Authorization headers nor upstream bodies enter logs.
        pass

    def error(self, status: int, message: str):
        body = json.dumps({"error": {"message": message, "type": "codex_gateway_error"}}).encode()
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def json_response(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(body)

    def provider_path(self):
        path = urlsplit(self.path).path
        parts = path.strip("/").split("/")
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

    def do_POST(self):
        try:
            if self.path == "/__corki_gateway/stop":
                supplied = self.headers.get("X-Corki-Control", "")
                token = self.server.control_token
                if not token or not hmac.compare_digest(supplied.encode(), token.encode()):
                    return self.error(403, "Gateway control denied.")
                self.json_response(200, {"stopping": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            route = self.provider_path()
            if route is not None:
                provider, endpoint = route
                getattr(self, PROVIDER_ROUTES[provider])(endpoint)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True

    def forward_qoder(self, endpoint):
        from corki_openai_gateway.qoder import forward

        return forward(self, endpoint)

    def forward_openai(self, endpoint):
        self.close_connection = True
        supplied = self.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied.encode(), ("Bearer " + self.server.key).encode()):
            return self.error(401, "Invalid local gateway key.")
        chat = endpoint in {"/chat/completions", "/v1/chat/completions"}
        if not chat and endpoint not in {"/responses", "/v1/responses"}:
            return self.error(404, "Unsupported gateway endpoint.")
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) != 1:
            return self.error(400, "A single Content-Length is required.")
        try:
            length = int(lengths[0])
        except ValueError:
            return self.error(400, "Invalid Content-Length.")
        if not 0 < length <= MAX_BODY:
            return self.error(413, "Request body exceeds gateway limits.")
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                return self.error(400, "Incomplete request body.")
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            return self.error(400, "Invalid JSON request.")
        if not isinstance(payload, dict) or not isinstance(payload.get("stream", False), bool):
            return self.error(400, "stream must be a boolean.")
        streaming = payload.get("stream", False)
        if chat:
            try:
                payload = to_responses(payload)
            except (ValueError, TypeError, KeyError, AttributeError):
                return self.error(400, "Unsupported Chat Completions request.")
        if not isinstance(payload.get("model"), str) or not payload["model"].strip():
            return self.error(400, "A nonempty model name is required.")
        # Native Codex core/client.rs sets store=false and stream=true. Preserve
        # instructions, tool definitions/results, reasoning and encrypted items.
        payload["store"] = False
        payload["stream"] = True
        started = False
        try:
            credentials = self.server.credentials()
            for attempt in range(2):
                headers = {
                    "Authorization": "Bearer " + credentials.access_token,
                    "ChatGPT-Account-ID": credentials.account_id,
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                }
                with self.server.client.stream(
                    "POST", UPSTREAM, json=payload, headers=headers
                ) as r:
                    if r.status_code == 401 and attempt == 0:
                        refreshed = self.server.credentials()
                        if refreshed.access_token != credentials.access_token:
                            credentials = refreshed
                            continue
                    if r.status_code != 200:
                        message = (
                            "Codex authentication expired or was rejected. Update the Codex login "
                            "and retry; the gateway does not rotate Codex refresh tokens."
                            if r.status_code == 401
                            else f"Codex upstream returned HTTP {r.status_code}."
                        )
                        status = r.status_code if 400 <= r.status_code < 600 else 502
                        return self.error(status, message)
                    content_type = r.headers.get("content-type")
                    # Some network relays omit Content-Type while preserving
                    # valid SSE. Codex consumes the body as events; let the
                    # downstream parser require response.completed as usual.
                    if content_type and not content_type.startswith("text/event-stream"):
                        return self.error(502, "Codex upstream did not return an event stream.")
                    if not streaming:
                        response = completed_response(r.iter_lines())
                        result = chat_completion(response, payload["model"]) if chat else response
                        body = json.dumps(result, ensure_ascii=False).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json; charset=utf-8")
                        self.send_header("Content-Length", str(len(body)))
                        self.send_header("Connection", "close")
                        self.end_headers()
                        started = True
                        self.wfile.write(body)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    started = True
                    chunks = (
                        chat_events(r.iter_lines(), payload["model"]) if chat else r.iter_bytes()
                    )
                    for chunk in chunks:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    return
        except CredentialsUnavailable as exc:
            if not started:
                self.error(401, str(exc))
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
            if not started:
                cause = exc
                seen = set()
                while cause is not None and id(cause) not in seen:
                    seen.add(id(cause))
                    if isinstance(cause, ssl.SSLCertVerificationError):
                        return self.error(
                            424,
                            "Codex upstream certificate verification failed. "
                            "Check the system trust store; retrying will not fix this.",
                        )
                    cause = cause.__cause__ or cause.__context__
                self.error(
                    502,
                    "Cannot reach Codex upstream; check network connectivity."
                    if isinstance(exc, httpx.HTTPError)
                    else "Codex upstream returned an invalid or incomplete response.",
                )
            # After headers, close the incomplete SSE stream. Never synthesize
            # response.completed or retry a request that may have executed.


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
