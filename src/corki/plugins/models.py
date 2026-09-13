"""Validated plugin manifest types."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from corki.config import MCPServerSettings
from corki.config.mcp_headers import RUST_WHITESPACE
from corki.plugins.hooks import PluginHookFile


@dataclass(frozen=True, slots=True)
class PluginManifest:
    name: str
    version: str | None
    description: str | None
    root: Path
    entrypoint: str | None = None
    skills_path: Path | None = None
    mcp_servers: tuple[MCPServerSettings, ...] = ()
    mcp_raw_names: tuple[tuple[str, str], ...] = ()
    mcp_warnings: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    agent_plugin: bool = False
    interface_display_name: str | None = None
    plugin_id: str | None = None
    enabled: bool = True
    error: str | None = None
    extra_skill_paths: tuple[Path, ...] = ()
    hook_sources: tuple[PluginHookFile, ...] = ()

    @property
    def identity(self) -> str:
        """Installation policy identity is independent of the manifest namespace."""
        return self.plugin_id if self.plugin_id is not None else self.name

    @property
    def skill_paths(self) -> tuple[Path, ...]:
        return (() if self.skills_path is None else (self.skills_path,)) + self.extra_skill_paths

    @property
    def display_name(self) -> str:
        """Presentation is distinct from the stable manifest/permission identity."""
        return (self.interface_display_name or "").strip(RUST_WHITESPACE) or self.name


@dataclass(frozen=True, slots=True)
class LoadedPlugin:
    manifest: PluginManifest
    tool_names: tuple[str, ...] = ()
