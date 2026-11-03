"""Read-only Codex login credentials."""
import json
import os
from dataclasses import dataclass
from pathlib import Path

from corki_adaptor.common import CredentialsUnavailable


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




class CredentialReader:
    """Keep one Codex account identity while allowing token rotation."""

    def __init__(self, provider):
        self.provider = provider
        self.account_id = None

    def __call__(self):
        credentials = self.provider()
        if self.account_id is None:
            self.account_id = credentials.account_id
        elif credentials.account_id != self.account_id:
            raise CredentialsUnavailable("Codex account changed. Restart the gateway to use it.")
        return credentials
