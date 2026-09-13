"""Atomic edits of host-selected config paths without discarding TOML decoration."""

import os
import tempfile
import threading
from collections.abc import MutableMapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import tomlkit
from tomlkit.items import InlineTable

from corki.config.features import MCPServerSettings, parse_mcp_servers

# Serialize read/modify/replace across Runtime instances in this process. This
# does not claim a transaction with unrelated editors in other processes.
_WRITES = threading.Lock()


def write_paths(path: Path) -> tuple[Path | None, Path]:
    """Match native final-component symlink traversal and its original-path fallback."""
    root = Path(os.path.abspath(path))
    current, visited = root, set()
    while True:
        try:
            current.lstat()
        except FileNotFoundError:
            return current, current
        except OSError:
            return None, root
        if not current.is_symlink():
            return current, current
        if current in visited:
            return None, root
        visited.add(current)
        try:
            target = current.readlink()
        except OSError:
            return None, root
        current = Path(os.path.abspath(target if target.is_absolute() else current.parent / target))


def set_config_value(path: Path, segments: tuple[str, ...], value: str) -> None:
    """Edit literal table keys; callers supply an authorized path, never tool input."""
    if not segments or any(not isinstance(segment, str) for segment in segments):
        raise ValueError("config edits require literal string keys")
    with _WRITES:
        read_path, write_path = write_paths(path)
        contents = ""
        if read_path is not None:
            with suppress(FileNotFoundError):
                contents = read_path.read_text(encoding="utf-8")
        document = tomlkit.parse(contents)
        table = document
        for segment in segments[:-1]:
            if segment not in table:
                table[segment] = (
                    tomlkit.inline_table()
                    if isinstance(table, InlineTable)
                    else tomlkit.table(is_super_table=True)
                )
            table = table[segment]
            if not isinstance(table, MutableMapping):
                raise ValueError("config edit parent is not a table")
        table[segments[-1]] = value
        _write_document(write_path, document)


@dataclass(frozen=True, slots=True)
class MCPServerInstallResult:
    """The exact global snapshot of a successful edit, not a later file reread."""

    added: tuple[str, ...]
    servers: tuple[MCPServerSettings, ...]


def add_missing_mcp_servers(path: Path, candidates: dict[str, dict]) -> MCPServerInstallResult:
    """One authorized batch; retain unrelated keys and never overwrite an existing name."""
    with _WRITES:
        read_path, write_path = write_paths(path)
        contents = ""
        if read_path is not None:
            with suppress(FileNotFoundError):
                contents = read_path.read_text(encoding="utf-8")
        document = tomlkit.parse(contents)
        mcp = document.get("mcp", {})
        if not isinstance(mcp, MutableMapping):
            raise ValueError("MCP configuration must be a table")
        servers = mcp.get("servers", {})
        existing = parse_mcp_servers(servers)
        added = tuple(name for name in candidates if name not in servers)
        if not added:
            return MCPServerInstallResult((), existing)
        snapshot = parse_mcp_servers({**servers, **{name: candidates[name] for name in added}})
        if "mcp" not in document:
            document["mcp"] = tomlkit.table(is_super_table=True)
        if "servers" not in document["mcp"]:
            document["mcp"]["servers"] = tomlkit.table(is_super_table=True)
        for name in added:
            document["mcp"]["servers"][name] = candidates[name]
        _write_document(write_path, document)
        return MCPServerInstallResult(added, snapshot)


def _write_document(write_path: Path, document) -> None:
    updated = tomlkit.dumps(document)
    write_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".corki-config-", dir=write_path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(updated)
        os.replace(temporary, write_path)
    finally:
        temporary.unlink(missing_ok=True)
