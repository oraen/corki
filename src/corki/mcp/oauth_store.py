"""Resolve credential policy once, then pin reads and refresh writes to that source."""

import asyncio
import hashlib
import json
import logging
from dataclasses import asdict

from corki.mcp.json_rpc import MCPProtocolError
from corki.mcp.oauth_file import FileOAuthAuthority, FileOAuthToken, _entries, _save
from corki.mcp.oauth_owned import join_oauth_task
from corki.protocol.wire_json import loads_wire

SERVICE = "Corki MCP Credentials"
_LOG = logging.getLogger(__name__)


def credential_key(name, url):
    return hashlib.sha256(json.dumps([name, url]).encode()).hexdigest()


def save_login_credentials(home, token, scopes, mode):
    """Login-only store selection; caller holds the shared credential identity lock."""
    if mode == "file":
        _save(home, None, token, scopes)
        return
    if mode not in {"auto", "keyring"}:
        raise ValueError("Unknown MCP OAuth credential store mode")
    authority = KeyringOAuthAuthority(home)
    try:
        authority._backend().set_password(
            SERVICE,
            credential_key(token.server_name, token.server_url),
            json.dumps(
                {**asdict(token), "scopes": scopes or []}, ensure_ascii=False, allow_nan=False
            ),
        )
    except Exception as error:
        if mode != "auto":
            raise
        _LOG.warning("OAuth login falling back to File: %s", type(error).__name__)
        _save(home, None, token, scopes)
        return
    try:
        _save(home, None, token, None, remove=True)
    except Exception as error:
        # Native login treats cleanup as best-effort after successful Keyring save.
        # Never replace the new Keyring token with an old File value or redo exchange.
        _LOG.warning("OAuth fallback File cleanup failed: %s", type(error).__name__)


class KeyringOAuthAuthority:
    def __init__(self, home):
        self.home = home
        self.backend = None

    def _backend(self):
        if self.backend is None:
            import keyring

            backend = keyring.get_keyring()
            if backend.priority <= 0:
                raise MCPProtocolError("OAuth keyring backend is unavailable")
            self.backend = backend
        return self.backend

    def _entry(self, name, url):
        raw = self._backend().get_password(SERVICE, credential_key(name, url))
        if raw is None:
            return None
        value = _entries(loads_wire('{"credential":' + raw + "}"))["credential"]
        if (
            value["server_name"] != name
            or value["server_url"] != url
            or value.get("executor_owned", False)
            or name.startswith(("executor:", "local:", "ema-idp:"))
        ):
            raise MCPProtocolError("OAuth keyring credential identity mismatch")
        return value

    def _load(self, name, url):
        entry = self._entry(name, url)
        return (
            None
            if entry is None
            else FileOAuthToken(
                **{field: entry.get(field) for field in FileOAuthToken.__dataclass_fields__}
            )
        )

    async def load(self, name, url):
        return await join_oauth_task(asyncio.create_task(asyncio.to_thread(self._load, name, url)))

    def _save(self, previous, updated, scopes):
        # The refresh transaction holds the shared per-identity lock around this CAS.
        entry = self._entry(previous.server_name, previous.server_url)
        if entry is None or any(entry.get(k) != v for k, v in asdict(previous).items()):
            raise MCPProtocolError("OAuth credentials changed before refresh persistence")
        value = {**entry, **asdict(updated), **({"scopes": scopes} if scopes is not None else {})}
        self._backend().set_password(
            SERVICE,
            credential_key(previous.server_name, previous.server_url),
            json.dumps(value, ensure_ascii=False, allow_nan=False),
        )

    async def save(self, previous, updated, scopes=None):
        await join_oauth_task(
            asyncio.create_task(asyncio.to_thread(self._save, previous, updated, scopes))
        )


class OAuthCredentialAuthority:
    def __init__(self, home, mode):
        if mode not in {"auto", "file", "keyring"}:
            raise ValueError("Unknown MCP OAuth credential store mode")
        self.home, self.mode = home, mode
        self.file = FileOAuthAuthority(home)
        self.keyring = KeyringOAuthAuthority(home)
        self.resolved = self.file if mode == "file" else self.keyring if mode == "keyring" else None

    async def load(self, name, url):
        (self.home / "mcp-oauth-locks").mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.resolved is not None:
            return await self.resolved.load(name, url)
        try:
            token = await self.keyring.load(name, url)
        except Exception as error:
            _LOG.warning("Initial OAuth keyring read failed: %s", type(error).__name__)
            token = None
        if token is not None:
            self.resolved = self.keyring
            return token
        token = await self.file.load(name, url)
        if token is not None:
            self.resolved = self.file
        return token

    async def save(self, previous, updated, scopes=None):
        if self.resolved is None:
            raise MCPProtocolError("OAuth refresh has no resolved credential authority")
        await self.resolved.save(previous, updated, scopes)
