"""Validated plugin manifest types."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from corki.config import MCPServerSettings


@dataclass(frozen=True, slots=True)
class PluginManifest:
    name: str
    version: str
    description: str
    root: Path
    entrypoint: str | None = None
    skills_path: Path | None = None
    mcp_servers: tuple[MCPServerSettings, ...] = ()


@dataclass(frozen=True, slots=True)
class LoadedPlugin:
    manifest: PluginManifest
    tool_names: tuple[str, ...] = ()
