"""Public-client OAuth login owned by the installing Runtime, never model auth."""

import asyncio
import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode

from corki.config.mcp_url import agent_url
from corki.http_client import OwnedHTTPClient
from corki.mcp.http_headers import build_http_headers
from corki.mcp.http_stream import owned_response
from corki.mcp.json_rpc import MCPProtocolError
from corki.mcp.oauth_callback import callback_listener
from corki.mcp.oauth_file import FileOAuthToken, _save
from corki.mcp.oauth_metadata import origin
from corki.mcp.oauth_owned import join_oauth_task
from corki.mcp.oauth_store import save_login_credentials
from corki.protocol.wire_json import check_fields, loads_wire, materialize
from corki.storage.file_lock import open_lock, try_lock, unlock_and_close


async def _post(client, endpoint, *, fields, headers=None, **payload):
    async with asyncio.timeout(45):
        request = client.build_request("POST", endpoint, headers=headers, **payload)
        response = await client.send(request, stream=True, follow_redirects=False)
        async with owned_response(response):
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > 1024 * 1024:
                    raise MCPProtocolError("OAuth response exceeds 1 MiB")
                body.extend(chunk)
        if not 200 <= response.status_code < 300:
            raise MCPProtocolError(f"OAuth request failed (HTTP {response.status_code})")
        try:
            raw = loads_wire(bytes(body))
            check_fields(raw, fields)
            return materialize(raw)
        except (ValueError, UnicodeError):
            raise MCPProtocolError("OAuth response is invalid") from None


async def _finish(
    client, endpoint, settings, home, issuer, data, scopes, ensure_admitted, store_mode
):
    # Serialize login publication with refresh. The lock spans token exchange
    # and durable save, so an older refresh cannot overwrite this new login.
    directory = home / "mcp-oauth-locks"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    identity = json.dumps([settings.name, settings.url]).encode()
    lock = open_lock(directory / (hashlib.sha256(identity).hexdigest() + ".lock"))
    acquired = False
    try:
        async with asyncio.timeout(60):
            while not (acquired := try_lock(lock)):
                await asyncio.sleep(0.05)
        # Waiting for another refresh/login must not preserve withdrawn authority.
        ensure_admitted()
        value = await _post(
            client,
            endpoint,
            data=data,
            fields={"access_token", "token_type", "refresh_token", "expires_in", "scope"},
        )
        try:
            access = value["access_token"]
            refresh = value.get("refresh_token")
            expires = value.get("expires_in")
            if (
                not isinstance(access, str)
                or not access
                or any(not 33 <= ord(c) <= 126 for c in access)
            ):
                raise ValueError
            if (
                not isinstance(value.get("token_type"), str)
                or value["token_type"].lower() != "bearer"
            ):
                raise ValueError
            if refresh is not None and (not isinstance(refresh, str) or not refresh.strip()):
                raise ValueError
            if expires is not None and (type(expires) is not int or not 0 <= expires < 1 << 53):
                raise ValueError
            granted = value.get("scope")
            if granted is not None and not isinstance(granted, str):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise MCPProtocolError("OAuth token response is invalid") from None
        token = FileOAuthToken(
            settings.name,
            settings.url,
            data["client_id"],
            access,
            issuer,
            refresh,
            None if expires is None else int(time.time() * 1000) + expires * 1000,
        )
        saved_scopes = scopes if granted is None else granted.split()
        if store_mode == "file":
            await asyncio.to_thread(_save, home, None, token, saved_scopes)
        else:
            await asyncio.to_thread(save_login_credentials, home, token, saved_scopes, store_mode)
    finally:
        if acquired:
            unlock_and_close(lock)
        else:
            lock.close()


async def login_oauth(
    settings, context, home, router, discovery, *, is_admitted=None, store_mode="file"
):
    """Complete a local public-client login using the explicitly selected store policy.

    Non-local credential identities and store policies are not aliased to local
    File. The caller must select the store policy before entering this transaction.
    """

    if store_mode not in {"auto", "file", "keyring"}:
        raise ValueError("Unknown MCP OAuth credential store mode")
    owner = asyncio.current_task()
    initial_cancellations = owner.cancelling()

    def ensure_admitted():
        # Drain submitted exchanges, but do not start one after cancellation
        # arrived while waiting for another credential transaction's lock.
        if owner.cancelling() > initial_cancellations:
            raise asyncio.CancelledError
        if is_admitted is not None and not is_admitted():
            raise MCPProtocolError("OAuth login authority changed before completion")

    ensure_admitted()
    if context.resolve(settings) is not None:
        raise MCPProtocolError("OAuth login for remote credential identities is not implemented")
    metadata = discovery.metadata
    authorize = agent_url(metadata["authorization_endpoint"])
    endpoint = agent_url(metadata["token_endpoint"])
    issuer = metadata.get("issuer")
    required = metadata.get("authorization_response_iss_parameter_supported") is True
    if required and (not isinstance(issuer, str) or not issuer.strip()):
        raise MCPProtocolError("OAuth issuer-bound callback requires an issuer")
    if (
        not required
        and origin(authorize) != origin(endpoint)
        and (not issuer or origin(authorize) != origin(agent_url(issuer)))
    ):
        raise MCPProtocolError("OAuth authorization endpoint is not bound to its issuer")
    for key, needed in (
        ("response_types_supported", "code"),
        ("code_challenge_methods_supported", "S256"),
    ):
        if metadata.get(key) is not None and needed not in metadata[key]:
            raise MCPProtocolError("OAuth server does not support authorization code with S256")
    client_id = settings.oauth.client_id if settings.oauth else None
    client_id = client_id if client_id and client_id.strip() else None
    scopes = list(
        settings.scopes if settings.scopes is not None else discovery.scopes_supported or ()
    )
    if (
        client_id is None
        and "offline_access" in (metadata.get("scopes_supported") or ())
        and "offline_access" not in scopes
    ):
        scopes.append("offline_access")
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    client = OwnedHTTPClient(timeout=None)
    try:
        async with asyncio.timeout(300):
            async with callback_listener(settings, metadata, state) as (redirect, callback):
                ensure_admitted()
                if client_id is None:
                    registration = metadata.get("registration_endpoint")
                    if not registration:
                        raise MCPProtocolError(
                            "OAuth requires a configured client_id or DCR endpoint"
                        )
                    registration = agent_url(registration)
                    headers, _ = build_http_headers(settings)
                    value = await _post(
                        client,
                        registration,
                        headers=headers if origin(registration) == origin(settings.url) else {},
                        json={
                            "client_name": "Corki",
                            "redirect_uris": [redirect],
                            "grant_types": ["authorization_code", "refresh_token"],
                            "response_types": ["code"],
                            "token_endpoint_auth_method": "none",
                            **({"scope": " ".join(scopes)} if scopes else {}),
                        },
                        fields={"client_id", "client_secret", "token_endpoint_auth_method"},
                    )
                    client_id = value.get("client_id")
                    if not isinstance(client_id, str) or not client_id.strip():
                        raise MCPProtocolError("OAuth registration returned an invalid client_id")
                    if (
                        value.get("client_secret") not in (None, "")
                        or value.get("token_endpoint_auth_method", "none") != "none"
                    ):
                        raise MCPProtocolError(
                            "OAuth confidential client registration is not implemented"
                        )
                ensure_admitted()
                resource = discovery.resource or settings.url
                query = [
                    ("response_type", "code"),
                    ("client_id", client_id),
                    ("redirect_uri", redirect),
                    ("state", state),
                    ("code_challenge", challenge),
                    ("code_challenge_method", "S256"),
                    ("resource", resource),
                ]
                if scopes:
                    query.append(("scope", " ".join(scopes)))
                if settings.oauth_resource and settings.oauth_resource.strip():
                    query.append(("resource", settings.oauth_resource.strip()))
                url = authorize + ("&" if "?" in authorize else "?") + urlencode(query)
                answer = await router.request(
                    settings.name,
                    {
                        "mode": "url",
                        "url": url,
                        "elicitationId": state,
                        "message": (
                            "Authorize this MCP service in your browser, then accept to finish."
                        ),
                    },
                )
                if answer.get("action") != "accept":
                    return False
                code = await callback
                ensure_admitted()
                await join_oauth_task(
                    asyncio.create_task(
                        _finish(
                            client,
                            endpoint,
                            settings,
                            home,
                            issuer,
                            {
                                "grant_type": "authorization_code",
                                "code": code,
                                "client_id": client_id,
                                "redirect_uri": redirect,
                                "code_verifier": verifier,
                                "resource": resource,
                            },
                            scopes,
                            ensure_admitted,
                            store_mode,
                        )
                    )
                )
                # A submitted exchange is drained and saved to its pinned identity,
                # but does not grant executable authority after host withdrawal.
                ensure_admitted()
                return True
    finally:
        await join_oauth_task(asyncio.create_task(client.aclose()))
