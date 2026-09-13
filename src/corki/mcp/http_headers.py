"""Local HTTP MCP header snapshots and safe invalid-entry fallback."""

import logging
import os
import re
from collections.abc import Mapping

import httpx

from corki import __version__
from corki.config import MCPServerSettings
from corki.config.mcp_headers import RUST_WHITESPACE

logger = logging.getLogger(__name__)
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9a-zA-Z-]+\Z", re.ASCII)
_WHITESPACE = RUST_WHITESPACE


def _value_bytes(value: str) -> bytes | None:
    try:
        data = value.encode("utf-8")
    except UnicodeError:
        return None
    return data if all(byte == 9 or byte >= 32 and byte != 127 for byte in data) else None


def build_http_headers(
    settings: MCPServerSettings, *, inherited: Mapping[str, str] | None = None
) -> tuple[httpx.Headers, str | None]:
    inherited = os.environ if inherited is None else inherited
    bearer = None
    if settings.bearer_token_env_var is not None:
        name = settings.bearer_token_env_var
        bearer = inherited.get(name)
        reason = "is not set" if bearer is None else "is empty" if bearer == "" else None
        if reason is None:
            try:
                bearer.encode("utf-8")
            except UnicodeError:
                reason = "contains invalid Unicode"
        if reason:
            raise ValueError(
                f"Environment variable {name} for MCP server '{settings.name}' {reason}"
            )
        if _value_bytes(f"Bearer {bearer}") is None:
            raise ValueError(f"Invalid bearer header for MCP server '{settings.name}'")

    headers = httpx.Headers({"user-agent": f"corki-mcp-client/{__version__}"}, encoding="utf-8")

    def insert(name: str, value: str) -> None:
        data = _value_bytes(value)
        if not _HEADER_NAME.fullmatch(name) or data is None:
            # Log the configured name, never values or exception messages containing tokens.
            logger.warning("Skipping invalid MCP HTTP header %r", name)
            return
        headers[name] = value

    for name, value in (
        settings.http_headers if settings.http_headers is not None else settings.headers
    ):
        insert(name, value)
    for name, reference in settings.env_http_headers or ():
        value = inherited.get(reference)
        if value is not None and value.strip(_WHITESPACE):
            try:
                value.encode("utf-8")
            except UnicodeError:
                continue
            insert(name, value)
    return headers, bearer


def request_headers(
    defaults: httpx.Headers,
    bearer: str | None,
    *,
    post: bool,
    protocol_version: str | None,
    session: str | None,
) -> httpx.Headers:
    headers = httpx.Headers(defaults, encoding="utf-8")
    if protocol_version is not None:
        headers["mcp-protocol-version"] = protocol_version
    if post:
        headers["accept"] = "text/event-stream, application/json"
        headers["content-type"] = "application/json"
    if bearer is not None:
        headers["authorization"] = f"Bearer {bearer}"
    if session is not None:
        headers["mcp-session-id"] = session
    return headers
