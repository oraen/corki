"""Owned read-refresh-write transactions for ordinary third-party MCP OAuth."""

import asyncio
import hashlib
import json
import time
from contextlib import suppress
from dataclasses import replace

import httpx

from corki.config.mcp_url import agent_url
from corki.http_client import OwnedHTTPClient
from corki.mcp.http_recovery import BorrowedHttpTransport
from corki.mcp.http_stream import owned_response
from corki.mcp.json_rpc import MCPProtocolError
from corki.mcp.oauth_discovery import discover_oauth
from corki.protocol.wire_json import check_fields, loads_wire, materialize
from corki.storage.file_lock import open_lock, try_lock, unlock_and_close


async def refresh_oauth(authority, previous, settings, context):
    """Join persistence even when the caller cancels after possible token rotation."""
    task = asyncio.create_task(_transaction(authority, previous, settings, context))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled:
        with suppress(Exception, asyncio.CancelledError):
            task.result()
        raise asyncio.CancelledError
    return task.result()


async def _transaction(authority, previous, settings, context):
    identity = json.dumps([previous.server_name, previous.server_url]).encode()
    lock = open_lock(
        authority.home / "mcp-oauth-locks" / (hashlib.sha256(identity).hexdigest() + ".lock")
    )
    acquired = False
    try:
        async with asyncio.timeout(60):
            while not (acquired := try_lock(lock)):
                await asyncio.sleep(0.05)
        latest = await authority.load(previous.server_name, previous.server_url)
        if latest is None:
            raise MCPProtocolError("OAuth authorization required: credentials were removed")
        if latest.refresh_token and (not latest.issuer or latest.issuer != previous.issuer):
            raise MCPProtocolError("OAuth authorization required: refresh issuer binding failed")
        if latest.usable():
            return latest
        if not latest.refresh_token or not latest.refresh_token.strip() or not latest.issuer:
            raise MCPProtocolError("OAuth authorization required: credentials cannot be refreshed")
        discovery = await discover_oauth(settings, context, local_timeout=30.0)
        if discovery is None or discovery.metadata.get("issuer") != latest.issuer:
            raise MCPProtocolError("OAuth authorization required: refresh issuer binding failed")
        endpoint = agent_url(discovery.metadata["token_endpoint"])
        environment = context.resolve(settings)
        # No model key, MCP headers, helper headers or access token enters the
        # independent token client. Redirects must never carry a refresh token.
        client = OwnedHTTPClient(
            transport=BorrowedHttpTransport(environment.transport) if environment else None,
            timeout=None,
        )
        try:
            async with asyncio.timeout(45):
                request = client.build_request(
                    "POST",
                    endpoint,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": latest.client_id,
                        "refresh_token": latest.refresh_token,
                        **({"resource": discovery.resource} if discovery.resource else {}),
                    },
                    extensions={"corki_executor_timeout_ms": 45000},
                )
                response = await client.send(request, stream=True, follow_redirects=False)
                async with owned_response(response):
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > 1024 * 1024:
                            raise MCPProtocolError("OAuth token response exceeds 1 MiB")
                        body.extend(chunk)
                if response.status_code != 200:
                    # Never include response bodies (which can echo credentials).
                    if response.status_code in {400, 401}:
                        with suppress(ValueError, TypeError):
                            error_body = loads_wire(bytes(body))
                            check_fields(error_body, {"error"})
                            if error_body.get("error") in {"invalid_grant", "invalid_client"}:
                                raise MCPProtocolError(
                                    "OAuth authorization required: refresh credentials rejected"
                                )
                    raise MCPProtocolError(
                        f"OAuth token request failed (HTTP {response.status_code})"
                    )
                try:
                    raw = loads_wire(bytes(body))
                    check_fields(
                        raw, {"access_token", "token_type", "expires_in", "refresh_token", "scope"}
                    )
                    value = materialize(raw)
                    access = value["access_token"]
                    if (
                        not isinstance(access, str)
                        or not access
                        or any(not (33 <= ord(c) <= 126) for c in access)
                    ):
                        raise ValueError
                    if (
                        not isinstance(value.get("token_type"), str)
                        or value["token_type"].lower() != "bearer"
                    ):
                        raise ValueError
                    expires = value.get("expires_in")
                    if expires is not None and (
                        type(expires) is not int or not 0 <= expires < 1 << 53
                    ):
                        raise ValueError
                    refresh = value.get("refresh_token")
                    if refresh is None:
                        refresh = latest.refresh_token
                    if not isinstance(refresh, str) or not refresh.strip():
                        raise ValueError
                    scope = value.get("scope")
                    if scope is not None and not isinstance(scope, str):
                        raise ValueError
                except (ValueError, KeyError, TypeError):
                    raise MCPProtocolError("OAuth token response is invalid") from None
                updated = replace(
                    latest,
                    access_token=access,
                    refresh_token=refresh,
                    expires_at=None
                    if expires is None
                    else int(time.time() * 1000) + expires * 1000,
                )
            # Provider timeout must not abandon a filesystem worker after rotation.
            await authority.save(latest, updated, scope.split() if scope is not None else None)
            return updated
        except httpx.HTTPError as error:
            raise MCPProtocolError(
                f"OAuth token transport failed: {type(error).__name__}"
            ) from None
        finally:
            await client.aclose()
    finally:
        if acquired:
            unlock_and_close(lock)
        else:
            lock.close()
