"""Active OAuth metadata discovery through the Runtime's selected HTTP authority.

This does not register clients, start authorization, or return credentials.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

import httpx

from corki.config.mcp_headers import RUST_WHITESPACE
from corki.config.mcp_url import http_url, join_http_url
from corki.http_client import OwnedHTTPClient
from corki.mcp.http_headers import build_http_headers
from corki.mcp.http_recovery import BorrowedHttpTransport
from corki.mcp.http_stream import owned_response
from corki.mcp.oauth_metadata import (
    OAuthDiscoveryError,
    allowed_authorization_server,
    authorization_urls,
    challenge_resource,
    expected_issuer,
    issuers_match,
    metadata_document,
    origin,
    resource_matches,
    resource_urls,
)

_LOG = logging.getLogger(__name__)
MAX_RESPONSE_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class OAuthDiscovery:
    metadata: dict
    source: str
    resource: str | None = None

    @property
    def scopes_supported(self) -> tuple[str, ...] | None:
        """Native discovery projection; do not mutate registration's raw metadata."""
        scopes = dict.fromkeys(
            normalized
            for scope in self.metadata.get("scopes_supported") or ()
            if (normalized := scope.strip(RUST_WHITESPACE))
        )
        return tuple(scopes) or None

    @property
    def callback_mode(self) -> Literal["issuer_bound", "callback_specific"]:
        """Discovery fallback only: login must separately validate the endpoints/issuer."""
        if (
            self.metadata.get("authorization_response_iss_parameter_supported") is True
            and self.metadata.get("issuer") is not None
        ):
            return "issuer_bound"
        return "callback_specific"


class _Discovery:
    def __init__(self, base, client, headers, timeout):
        self.base, self.client, self.headers, self.timeout = base, client, headers, timeout

    async def get(self, url):
        for _ in range(10):
            headers = (
                httpx.Headers(self.headers, encoding="utf-8")
                if origin(url) == origin(self.base)
                else httpx.Headers({"user-agent": self.default_agent})
            )
            headers["mcp-protocol-version"] = "2024-11-05"
            for _, value in headers.multi_items():
                value.encode("ascii")  # Native HttpHeader conversion requires HeaderValue.to_str.
            request = httpx.Request(
                "GET",
                url,
                headers=headers,
                extensions={"corki_executor_timeout_ms": int(self.timeout * 1000)},
            )
            async with asyncio.timeout(self.timeout):
                response = await self.client.send(request, stream=True, follow_redirects=False)
                async with owned_response(response):
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(chunk) > MAX_RESPONSE_BYTES - len(body):
                            raise OAuthDiscoveryError("OAuth metadata response exceeds 1 MiB")
                        body.extend(chunk)
                    data = bytes(body)
            status = response.status_code
            if 500 <= status < 600 or status in {408, 425, 429}:
                raise OAuthDiscoveryError("OAuth metadata request returned a transient HTTP error")
            if status == 200:
                parsed = metadata_document(data, authorization=True)
                if (
                    parsed is not None
                    and parsed.get("issuer") is not None
                    and origin(http_url(parsed["issuer"])) != origin(url)
                ):
                    raise OAuthDiscoveryError("OAuth metadata issuer origin mismatch")
            if not 300 <= status < 400 or "location" not in response.headers:
                return status, response.headers, data
            location = response.headers.get_list("location")[0]
            location.encode("ascii")
            target = join_http_url(url, location)
            if origin(target) != origin(url):
                raise OAuthDiscoveryError("OAuth discovery redirect origin mismatch")
            url = target
        raise OAuthDiscoveryError("OAuth discovery exceeded the redirect limit")

    @property
    def default_agent(self):
        from corki import __version__

        return f"corki-mcp-client/{__version__}"

    async def probe(self, url):
        status, headers, _ = await self.get(url)
        if status == 200:
            return url
        if status == 401:
            for header in headers.get_list("www-authenticate"):
                try:
                    header.encode("ascii")
                except UnicodeError:
                    continue
                if candidate := challenge_resource(header, self.base):
                    return candidate
        return None

    async def authorization(self, url, issuer=None):
        status, _, body = await self.get(url)
        parsed = metadata_document(body, authorization=True) if status == 200 else None
        if parsed is None:
            return None
        expected = issuer if issuer is not None else expected_issuer(url)
        # Discovery allows absent issuer; login has additional endpoint/binding checks.
        if (
            expected is not None
            and parsed.get("issuer") is not None
            and not issuers_match(parsed["issuer"], expected)
        ):
            raise OAuthDiscoveryError("OAuth metadata issuer does not match discovery identity")
        return parsed

    async def authorization_candidates(self, base, issuer=None):
        for url in authorization_urls(base):
            if (metadata := await self.authorization(url, issuer)) is not None:
                return metadata
        return None

    async def resource(self, url):
        status, _, body = await self.get(url)
        parsed = metadata_document(body, authorization=False) if status == 200 else None
        if parsed is None:
            return None
        resource = parsed.get("resource")
        if resource is None or not resource_matches(self.base, resource):
            raise OAuthDiscoveryError("OAuth protected resource identity mismatch")
        candidates = (
            [parsed["authorization_server"]]
            if parsed.get("authorization_server") is not None
            else []
        ) + (parsed.get("authorization_servers") or [])
        for candidate in candidates:
            candidate = candidate.strip(RUST_WHITESPACE)
            if not candidate:
                continue
            try:
                candidate = join_http_url(url, candidate)
            except ValueError:
                continue
            if not allowed_authorization_server(self.base, candidate):
                continue
            metadata = (
                await self.authorization(candidate)
                if "/.well-known/" in urlsplit(candidate).path
                else await self.authorization_candidates(candidate, candidate)
            )
            if metadata is not None:
                return OAuthDiscovery(metadata, "protected_resource", resource)
        return None

    async def resolve(self):
        resource_url = await self.probe(self.base)
        if resource_url is None:
            for url in resource_urls(self.base):
                if (resource_url := await self.probe(url)) is not None:
                    break
        if resource_url is not None and (result := await self.resource(resource_url)) is not None:
            return result
        if (metadata := await self.authorization_candidates(self.base)) is not None:
            return OAuthDiscovery(metadata, "authorization_server")
        # Synthesized legacy endpoints are not evidence that OAuth is supported.
        return None


async def discover_oauth(settings, runtime_context, *, local_timeout: float = 5.0):
    """None = not supported; exceptions = unknown. Never convert cancellation to either."""
    if settings.transport != "http" or settings.bearer_token_env_var is not None:
        return None
    environment = runtime_context.resolve(settings)
    base = http_url(settings.url)
    headers, _ = build_http_headers(settings)
    client = OwnedHTTPClient(
        transport=BorrowedHttpTransport(environment.transport) if environment is not None else None,
        timeout=None,
    )
    primary = None
    try:
        return await _Discovery(
            base, client, headers, local_timeout if environment is None else 30.0
        ).resolve()
    except BaseException as error:
        primary = error
        raise
    finally:
        close = asyncio.create_task(client.aclose(), name="mcp-oauth-discovery-close")
        cancelled = False
        while not close.done():
            try:
                await asyncio.shield(close)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        if cancelled:
            if not close.cancelled():
                close.exception()
            raise asyncio.CancelledError
        try:
            close.result()
        except Exception as error:
            if primary is None:
                raise
            _LOG.warning("OAuth discovery cleanup failed: %s", type(error).__name__)
