"""Pinned file credentials and atomic compare-and-save for OAuth refresh."""

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path

from corki.config.mcp_headers import RUST_WHITESPACE
from corki.mcp.json_rpc import MCPProtocolError
from corki.protocol.wire_json import WireObject, check_fields, loads_wire, materialize
from corki.storage.file_lock import open_lock, try_lock, unlock_and_close

_LOG = logging.getLogger(__name__)
_FIELDS = {
    "server_name",
    "server_url",
    "issuer",
    "client_id",
    "access_token",
    "expires_at",
    "refresh_token",
    "scopes",
    "executor_owned",
}


@dataclass(frozen=True, slots=True, repr=False)
class FileOAuthToken:
    server_name: str
    server_url: str
    client_id: str
    access_token: str
    issuer: str | None
    refresh_token: str | None
    expires_at: int | None

    def usable(self) -> bool:
        return bool(self.access_token.strip(RUST_WHITESPACE)) and (
            self.expires_at is None or int(time.time() * 1000) + 30_000 < self.expires_at
        )


def _read(home: Path, name: str, url: str) -> FileOAuthToken | None:
    """Read under the same aggregate authority needed by a future login writer."""
    directory = home / "mcp-oauth-locks"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = open_lock(directory / "file-store.lock")
    acquired = False
    try:
        deadline = time.monotonic() + 60
        while not (acquired := try_lock(lock, shared=True)):
            if time.monotonic() >= deadline:
                raise TimeoutError("OAuth file-store lock timed out")
            time.sleep(0.05)
        try:
            raw = loads_wire((home / ".credentials.json").read_bytes())
        except FileNotFoundError:
            return None
        entries = _entries(raw)
        # Ordinary host identities only. Executor and escaped identities require their
        # own key/owner contract, and must never accidentally consume host credentials.
        if name.startswith(("executor:", "local:", "ema-idp:")):
            raise MCPProtocolError("OAuth credential identity is not implemented")
        for _, value in sorted(entries.items()):
            if (
                not value.get("executor_owned", False)
                and value["server_name"] == name
                and value["server_url"] == url
            ):
                return FileOAuthToken(
                    **{field: value.get(field) for field in FileOAuthToken.__dataclass_fields__}
                )
        return None
    finally:
        if acquired:
            unlock_and_close(lock)
        else:
            lock.close()


def _entries(raw):
    # Validate every known field, including unrelated entries; retain unknown
    # fields so refresh never erases a newer writer's metadata.
    if not isinstance(raw, WireObject):
        raise ValueError("OAuth credential file must be a map")
    entries = {}
    for key, entry in raw.pairs:
        check_fields(entry, _FIELDS)
        value = materialize(entry)
        for field in ("server_name", "server_url", "client_id", "access_token"):
            if not isinstance(value.get(field), str):
                raise ValueError("OAuth credential string field is invalid")
        for field in ("issuer", "refresh_token"):
            if value.get(field) is not None and not isinstance(value[field], str):
                raise ValueError("OAuth credential optional string is invalid")
        expiry = value.get("expires_at")
        if expiry is not None and (type(expiry) is not int or not 0 <= expiry < 1 << 64):
            raise ValueError("OAuth credential expiry is invalid")
        scopes = value.get("scopes", [])
        if not isinstance(scopes, list) or any(not isinstance(s, str) for s in scopes):
            raise ValueError("OAuth credential scopes are invalid")
        if type(value.get("executor_owned", False)) is not bool:
            raise ValueError("OAuth credential owner is invalid")
        entries[key] = value
    return entries


def _save(home, previous, updated, scopes, *, remove=False):
    """Serialize aggregate writes without holding this lock during provider I/O."""
    lock = open_lock(home / "mcp-oauth-locks" / "file-store.lock")
    acquired = False
    temporary = None
    try:
        deadline = time.monotonic() + 60
        while not (acquired := try_lock(lock)):
            if time.monotonic() >= deadline:
                raise TimeoutError("OAuth file-store lock timed out")
            time.sleep(0.05)
        path = home / ".credentials.json"
        try:
            entries = _entries(loads_wire(path.read_bytes()))
        except FileNotFoundError:
            if previous is not None:
                raise
            entries = {}
        if remove:
            kept = {
                key: entry
                for key, entry in entries.items()
                if entry.get("executor_owned", False)
                or entry.get("server_name") != updated.server_name
                or entry.get("server_url") != updated.server_url
            }
            if len(kept) == len(entries):
                return
            entries = kept
        else:
            entries = _updated_entries(entries, previous, updated, scopes)
        descriptor, temporary = tempfile.mkstemp(prefix=".mcp-credentials-", dir=home)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(entries, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        if os.name != "nt":
            directory = os.open(home, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
        if acquired:
            unlock_and_close(lock)
        else:
            lock.close()


def _updated_entries(entries, previous, updated, scopes):
    identity = previous if previous is not None else updated
    found = False
    for key, entry in sorted(entries.items()):
        if (
            isinstance(entry, dict)
            and not entry.get("executor_owned", False)
            and entry.get("server_name") == identity.server_name
            and entry.get("server_url") == identity.server_url
        ):
            if previous is not None and any(
                entry.get(field) != getattr(previous, field)
                for field in previous.__dataclass_fields__
            ):
                raise MCPProtocolError("OAuth credentials changed before refresh persistence")
            entries[key] = {
                **entry,
                **(asdict(updated) if previous is None else {}),
                "access_token": updated.access_token,
                "refresh_token": updated.refresh_token,
                "expires_at": updated.expires_at,
                **({"scopes": scopes} if scopes is not None else {}),
            }
            found = True
            break
    if not found:
        if previous is not None:
            raise MCPProtocolError("OAuth credentials removed before refresh persistence")
        key = hashlib.sha256(
            json.dumps([updated.server_name, updated.server_url]).encode()
        ).hexdigest()
        # Never overwrite an unrelated entry that happens to occupy this key.
        while key in entries:
            key += "_"
        entries[key] = {**asdict(updated), "scopes": scopes or []}
    return entries


class FileOAuthAuthority:
    """One logical client's fixed source, shared by its HTTP recovery generations."""

    def __init__(self, home: Path):
        self.home = home
        self.pinned = False

    async def load(self, name: str, url: str) -> FileOAuthToken | None:
        task = asyncio.create_task(asyncio.to_thread(_read, self.home, name, url))
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
        try:
            token = task.result()
        except Exception as error:
            if self.pinned:
                raise
            _LOG.warning("Initial OAuth credential read failed: %s", type(error).__name__)
            return None
        if token is not None:
            self.pinned = True
        return token

    async def save(self, previous, updated, scopes=None):
        # The refresh owner's cancellation shield spans this worker and the
        # provider request; never publish updated credentials before it completes.
        await asyncio.to_thread(_save, self.home, previous, updated, scopes)
