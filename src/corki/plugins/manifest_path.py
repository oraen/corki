"""Host manifest admission, before choosing legacy or Agent Plugin parsing."""

import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from corki.protocol.wire_json import loads_wire, materialize

AGENT_SCHEMA_PREFIX = "https://agent-plugins.org/schemas/"
AGENT_SCHEMA = AGENT_SCHEMA_PREFIX + "1.0.0/plugin.schema.json"


@dataclass(frozen=True, slots=True)
class ManifestSource:
    """Format and root established by discovery, not inferred from package names."""

    path: Path
    root: Path
    agent_plugin: bool = False


def json_object(contents: str, *, value_object: bool = True) -> dict[str, Any]:
    """Read a JSON object without Python's non-JSON numeric extensions."""

    value = loads_wire(contents)
    if value_object:
        value = materialize(value, preserve_pairs=False)
    if not isinstance(value, dict):
        raise ValueError("manifest must contain a JSON object")
    return value


def find_manifest(root: Path, *, native_only: bool = False) -> ManifestSource | None:
    """An unsupported Agent schema still selects the root; it cannot downgrade."""
    candidate = root / "plugin.json"
    try:
        if not stat.S_ISREG(candidate.lstat().st_mode):
            return None
    except FileNotFoundError:
        pass
    except OSError:
        return None
    else:
        try:
            schema = json_object(candidate.read_text(encoding="utf-8")).get("$schema")
        except (OSError, UnicodeError, ValueError):
            pass  # Unrelated/unreadable contents permit legacy fallback, unlike metadata.
        else:
            if isinstance(schema, str) and schema.startswith(AGENT_SCHEMA_PREFIX):
                return ManifestSource(candidate, root, agent_plugin=True)

    for relative in (
        ".corki-plugin/plugin.toml",
        ".codex-plugin/plugin.json",
        ".claude-plugin/plugin.json",
        ".cursor-plugin/plugin.json",
    ):
        if native_only and relative == ".corki-plugin/plugin.toml":
            continue
        candidate = root / relative
        try:
            if not stat.S_ISDIR(candidate.parent.lstat().st_mode):
                return None
            return (
                ManifestSource(candidate, root) if stat.S_ISREG(candidate.lstat().st_mode) else None
            )
        except FileNotFoundError:
            continue
        except OSError:
            return None
    return None
