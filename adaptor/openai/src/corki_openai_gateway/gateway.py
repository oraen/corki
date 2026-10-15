"""Opt-in, loopback-only Responses bridge for an existing Codex ChatGPT login.

Codex owns login and refresh. This bridge never writes or refreshes auth.json.
The downstream key is ephemeral and is not an OpenAI Platform API key.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import ssl
import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import truststore

from corki_openai_gateway.chat import chat_events, to_responses

UPSTREAM = "https://chatgpt.com/backend-api/codex/responses"
MAX_BODY = 16 * 1024 * 1024


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
        document = json.loads(path.read_text())
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
    ):
        self.auth_file = auth_file
        self.key = key
        if credential_provider is None:
            if auth_file is None:
                raise CredentialsUnavailable("An auth file or credential provider is required.")
            credential_provider = partial(read_credentials, auth_file)
        self.credential_provider = credential_provider
        self.account_id = credential_provider().account_id
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(120, connect=15),
            follow_redirects=False,
            # Match native Codex's OS trust: enterprise roots installed in the
            # system keychain must work without disabling peer/hostname checks.
            verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
        )
        self.owns_client = client is None
        super().__init__(("127.0.0.1", port), Handler)

    def credentials(self) -> Credentials:
        credentials = self.credential_provider()
        if credentials.account_id != self.account_id:
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

    def do_POST(self):
        try:
            self.forward()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True

    def forward(self):
        self.close_connection = True
        supplied = self.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied.encode(), ("Bearer " + self.server.key).encode()):
            return self.error(401, "Invalid local gateway key.")
        chat = self.path in {"/chat/completions", "/v1/chat/completions"}
        if not chat and self.path not in {"/responses", "/v1/responses"}:
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
        if not isinstance(payload, dict) or payload.get("stream") is not True:
            return self.error(400, "A streaming Responses request is required.")
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
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
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
                self.error(502, "Cannot reach Codex upstream; check network connectivity.")
            # After headers, close the incomplete SSE stream. Never synthesize
            # response.completed or retry a request that may have executed.


def main(argv: list[str] | None = None) -> int:
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
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=17001)
    parser.add_argument("--key-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        key = args.key_file.expanduser().read_text().strip()
    except OSError:
        parser.error("gateway key file is unavailable")
    if not key:
        parser.error("gateway key must not be empty")
    try:
        provider = None
        if args.access_token_env is not None:
            provider = partial(environment_credentials, args.access_token_env, args.account_id_env)
        gateway = Gateway(
            None if provider else args.auth_file.expanduser(),
            key,
            port=args.port,
            credential_provider=provider,
        )
    except CredentialsUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        gateway.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        gateway.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
