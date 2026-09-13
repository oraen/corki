"""Agent Plugin identity and fixed components; no executable legacy entrypoint overlay."""

import re
from dataclasses import replace
from pathlib import Path

from corki.config.mcp_headers import RUST_WHITESPACE
from corki.plugins.agent_mcp import load_agent_mcp
from corki.plugins.agent_overlay import parse_overlay
from corki.plugins.manifest_path import AGENT_SCHEMA, json_object
from corki.plugins.models import PluginManifest
from corki.plugins.presentation import interface_display_name
from corki.protocol.wire_numbers import dumps_wire

_FIELDS = {
    "$schema",
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "extensions",
}
_NAME = re.compile(r"[a-z0-9](?:[a-z0-9.\-]{0,62}[a-z0-9])?")


def parse_agent_manifest(path: Path, *, data_root: Path | None = None) -> PluginManifest:
    """Validate root metadata; presentation extensions do not replace capabilities."""
    root = path.parent.resolve(strict=True)
    value = json_object(path.read_text(encoding="utf-8"))
    warnings = [
        f"ignoring unknown Agent Plugin field {key}" for key in sorted(value.keys() - _FIELDS)
    ]
    if value.get("$schema") != AGENT_SCHEMA:
        raise ValueError("unsupported Agent Plugin schema")
    name = value.get("name")
    if not isinstance(name, str) or _NAME.fullmatch(name) is None or "--" in name or ".." in name:
        raise ValueError("invalid Agent Plugin name")
    for field in ("version", "description", "homepage", "repository", "license"):
        if field in value and not isinstance(value[field], str):
            raise ValueError(f"Agent Plugin {field} must be a string when present")
    if "author" in value:
        author = value["author"]
        if (
            not isinstance(author, dict)
            or author.keys() - {"name", "email", "url"}
            or not all(isinstance(v, str) for v in author.values())
        ):
            raise ValueError("invalid Agent Plugin author")
    keywords = value.get("keywords", [])
    if not isinstance(keywords, list) or not all(isinstance(v, str) for v in keywords):
        raise ValueError("Agent Plugin keywords must be a string array")
    extensions = value.get("extensions", {})
    if not isinstance(extensions, dict):
        warnings.append("ignoring non-object Agent Plugin extensions")
        extensions = {}
    extension = extensions.get("com.openai")
    if "com.openai" in extensions and not isinstance(extension, dict):
        warnings.append("ignoring non-object Agent Plugin com.openai extension")
    try:
        overlay = (root / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        overlay = None
    presentation = {}
    if isinstance(extension, dict):
        presentation = parse_overlay(dumps_wire(extension))
    elif overlay is not None:
        presentation = parse_overlay(overlay)
    mcp = load_agent_mcp(root, overlay, data_root=data_root)
    skills = root / "skills"
    return PluginManifest(
        name=name,
        version=value.get("version", "").strip(RUST_WHITESPACE) or None,
        description=value.get("description", "").strip(RUST_WHITESPACE) or None,
        root=root,
        skills_path=skills if skills.is_dir() else None,
        mcp_servers=tuple(replace(s, name=f"{name}__{s.name}") for s in mcp.servers),
        mcp_raw_names=tuple((f"{name}__{s.name}", s.name) for s in mcp.servers),
        mcp_warnings=mcp.warnings,
        warnings=tuple(warnings),
        agent_plugin=True,
        interface_display_name=interface_display_name(presentation),
    )
