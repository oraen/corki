"""Codex-style local stdio environment selection; not an OS sandbox."""

import os
from collections.abc import Mapping
from pathlib import Path

from corki.config import MCPEnvVar, MCPServerSettings

UNIX_DEFAULTS = (
    "HOME",
    "LOGNAME",
    "PATH",
    "SHELL",
    "USER",
    "__CF_USER_TEXT_ENCODING",
    "LANG",
    "LC_ALL",
    "TERM",
    "TMPDIR",
    "TZ",
)
WINDOWS_DEFAULTS = (
    "PATH",
    "PATHEXT",
    "SHELL",
    "COMSPEC",
    "SYSTEMROOT",
    "WINDIR",
    "SYSTEMDRIVE",
    "USERNAME",
    "USERDOMAIN",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMW6432",
    "PROGRAMDATA",
    "LOCALAPPDATA",
    "APPDATA",
    "TEMP",
    "TMP",
    "TMPDIR",
    "POWERSHELL",
    "PWSH",
)
CUSTOM_CA_KEYS = (
    "CODEX_CA_CERTIFICATE",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS",
    "GIT_SSL_CAINFO",
    "CARGO_HTTP_CAINFO",
    "PIP_CERT",
    "BUNDLE_SSL_CA_CERT",
    "npm_config_cafile",
    "NPM_CONFIG_CAFILE",
)
NON_INHERITABLE = (
    "CODEX_EXEC_SERVER_NOISE_AUTH_TOKEN",
    "NODE_REPL_AUTH_TOKEN",
    "OPENAI_FEDERATION_RULE_ID",
    "OPENAI_IDENTITY_TOKEN_FILE",
    "OPENAI_WORKLOAD_IDENTITY_CONTEXT",
)
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_BLOCKED = frozenset(name.translate(_ASCII_LOWER) for name in NON_INHERITABLE)
_CA = frozenset(name.translate(_ASCII_LOWER) for name in CUSTOM_CA_KEYS)


def build_stdio_environment(
    settings: MCPServerSettings,
    *,
    inherited: Mapping[str, str] | None = None,
    parent_cwd: Path | None = None,
    windows: bool | None = None,
) -> dict[str, str]:
    """Defaults + named variables + inherited CA paths + literals, then deny internal keys."""
    inherited = dict(os.environ) if inherited is None else inherited
    windows = os.name == "nt" if windows is None else windows
    names = list(WINDOWS_DEFAULTS if windows else UNIX_DEFAULTS)
    for ref in settings.env_vars:
        if isinstance(ref, MCPEnvVar):
            if ref.source == "remote":
                raise ValueError(
                    f"env_vars entry '{ref.name}' uses source remote, "
                    "which requires remote MCP stdio"
                )
            names.append(ref.name)
        else:
            names.append(ref)
    lookup = {k.translate(_ASCII_LOWER): v for k, v in inherited.items()} if windows else inherited

    def get(name: str) -> str | None:
        return lookup.get(name.translate(_ASCII_LOWER) if windows else name)

    result = {name: value for name in names if (value := get(name)) is not None}

    def insert(name: str, value: str, *, insensitive: bool) -> None:
        if insensitive:
            folded = name.translate(_ASCII_LOWER)
            for key in tuple(result):
                if key.translate(_ASCII_LOWER) == folded:
                    del result[key]
        result[name] = value

    for name in CUSTOM_CA_KEYS:
        value = get(name)
        if value:
            path = Path(value)
            if not path.is_absolute():
                path = (parent_cwd if parent_cwd is not None else Path.cwd()) / path
            insert(name, str(path), insensitive=windows)
    for name, value in settings.env:
        insert(name, value, insensitive=windows or name.translate(_ASCII_LOWER) in _CA)
    return {k: v for k, v in result.items() if k.translate(_ASCII_LOWER) not in _BLOCKED}
