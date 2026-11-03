"""Read Qoder credentials without writing, refreshing or exporting login state."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from corki_adaptor.common import CredentialsUnavailable


@dataclass(frozen=True)
class QoderCredentials:
    token: str = field(repr=False)
    identity: str


def credential(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid credential object")
    value = value.get("tokens", value)
    token = value.get("token") or value.get("security_oauth_token") or value.get("access_token")
    if not isinstance(token, str) or not token.strip() or "\n" in token or "\r" in token:
        raise ValueError("No bearer token")
    identity = value.get("id") or value.get("uid") or value.get("account_id") or "external"
    return QoderCredentials(token, str(identity))


class QoderCredentialReader:
    def __init__(self):
        self.lock = threading.RLock()
        self.password = None
        self.encrypted = None
        self.cached = None
        self.identity = None

    def __call__(self):
        with self.lock:
            try:
                result = self._read()
                if self.identity is not None and result.identity != self.identity:
                    raise CredentialsUnavailable("Qoder 账号已变化，请重启适配网关。")
                self.identity = result.identity
                return result
            except (
                OSError,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                sqlite3.Error,
                subprocess.SubprocessError,
            ):
                raise CredentialsUnavailable(
                    "未能读取 Qoder 登录信息。请登录 Qoder IDE 并允许系统钥匙串访问，"
                    "或设置 QODER_ACCESS_TOKEN / QODER_AUTH_FILE。"
                ) from None

    def _read(self):
        token = os.environ.get("QODER_ACCESS_TOKEN")
        if token:
            return credential({"token": token})
        explicit = os.environ.get("QODER_AUTH_FILE")
        if explicit:
            return credential(json.loads(Path(explicit).expanduser().read_text("utf-8")))
        home = Path(os.environ.get("QODER_HOME", Path.home() / ".qoder")).expanduser()
        auth = home / "credentials/auth.json"
        if auth.is_file():
            value = json.loads(auth.read_text("utf-8"))
            # Current IDE versions may leave only a 'cleared' state marker here.
            if isinstance(value, dict) and any(
                k in value for k in ("token", "tokens", "access_token")
            ):
                return credential(value)
        if sys.platform != "darwin":
            raise ValueError("Use an explicit token or credential file on this platform")
        configured = os.environ.get("QODER_IDE_STATE_DB")
        path = (
            Path(configured).expanduser()
            if configured
            else Path.home() / "Library/Application Support/Qoder/User/globalStorage/state.vscdb"
        )
        with closing(
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
        ) as db:
            row = db.execute(
                "SELECT value FROM ItemTable WHERE key=?", ("secret://aicoding.auth.userInfo",)
            ).fetchone()
        if row is None:
            raise ValueError("No IDE login")
        envelope = json.loads(row[0])
        encrypted = bytes(envelope["data"])
        if encrypted == self.encrypted and self.cached is not None:
            return self.cached
        if not encrypted.startswith(b"v10") or len(encrypted) > 1024 * 1024:
            raise ValueError("Unsupported IDE credential envelope")
        if self.password is None:
            result = subprocess.run(
                ["security", "find-generic-password", "-s", "Qoder Safe Storage", "-w"],
                capture_output=True,
                timeout=45,
                check=True,
            )
            self.password = result.stdout.rstrip(b"\n")
        # Chromium/Electron macOS safeStorage v10 format. The OS-protected
        # password is obtained through normal Keychain authorization, never bundled.
        from cryptography.hazmat.primitives import hashes, padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

        key = PBKDF2HMAC(
            algorithm=hashes.SHA1(), length=16, salt=b"saltysalt", iterations=1003
        ).derive(self.password)
        decryptor = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
        raw = decryptor.update(encrypted[3:]) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        value = json.loads(unpadder.update(raw) + unpadder.finalize())
        if isinstance(value, str):
            value = json.loads(value)
        result = credential(value)
        self.encrypted, self.cached = encrypted, result
        return result
