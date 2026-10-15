"""Native cache manifests never authorize Corki's in-process Python entrypoints."""

import os
from dataclasses import replace
from pathlib import Path

from corki.config.mcp_headers import RUST_WHITESPACE
from corki.plugins.agent_manifest import parse_agent_manifest
from corki.plugins.agent_overlay import parse_overlay
from corki.plugins.hooks import load_plugin_hooks
from corki.plugins.manifest_path import find_manifest
from corki.plugins.mcp import load_plugin_mcp
from corki.plugins.models import PluginManifest
from corki.plugins.presentation import interface_display_name
from corki.plugins.store import PluginInstallation


def installed_manifest(installation: PluginInstallation, home: Path) -> PluginManifest:
    identity, root = installation.identity, installation.root
    empty = PluginManifest(
        name=installation.plugin_id.name if installation.plugin_id else identity,
        version=None,
        description=None,
        root=root,
        plugin_id=identity,
        enabled=installation.enabled,
        error=installation.error,
    )
    if not installation.enabled or installation.error is not None:
        return empty
    try:
        source = find_manifest(root, native_only=True)
        if source is None:
            raise ValueError("missing or invalid plugin.json")
        if source.agent_plugin:
            manifest = parse_agent_manifest(
                source.path, data_root=installation.plugin_id.data_root(home, agent_plugin=True)
            )
            return replace(manifest, plugin_id=identity)
        # This parser already implements the native legacy metadata contract for
        # Agent overlays. Unknown fields, including entrypoint, have no authority.
        value = parse_overlay(source.path.read_text(encoding="utf-8"))
        name = value.get("name", "")
        name = name if name.strip(RUST_WHITESPACE) else root.name
        skills, warnings = [], []
        selected = value.get("skills")
        paths = [selected] if isinstance(selected, str) else selected
        if paths is not None and (
            not isinstance(paths, list) or any(not isinstance(p, str) for p in paths)
        ):
            warnings.append("ignoring skills: expected a string or string array")
            paths = []
        for path in paths or []:
            relative = path[2:] if path.startswith("./") else ""
            components = relative.replace("\\", "/") if os.name == "nt" else relative
            if not relative or ".." in components.split("/") or Path(relative).anchor:
                warnings.append("ignoring skills: expected a contained ./ path")
                continue
            skills.append(root / relative)
        if not skills and (root / "skills").is_dir():
            skills.append(root / "skills")
        # Native migrations store command-derived skills alongside normal roots.
        migrated = root / ".codex-plugin/migrated-command-skills"
        if migrated.is_dir():
            skills.append(migrated)
        skills = sorted(set(skills))
        mcp = load_plugin_mcp(root, {}, value)
        hook_sources, hook_warnings = load_plugin_hooks(
            root,
            value.get("hooks"),
            data_root=installation.plugin_id.data_root(home, agent_plugin=False),
        )
        return replace(
            empty,
            name=name,
            version=(value.get("version") or "").strip(RUST_WHITESPACE) or None,
            description=(value.get("description") or "").strip(RUST_WHITESPACE) or None,
            skills_path=skills[0] if skills else None,
            extra_skill_paths=tuple(skills[1:]),
            mcp_servers=mcp.servers,
            mcp_raw_names=tuple((server.name, server.name) for server in mcp.servers),
            mcp_warnings=mcp.warnings,
            warnings=(*warnings, *hook_warnings),
            hook_sources=hook_sources,
            interface_display_name=interface_display_name(value),
        )
    except (OSError, UnicodeError, ValueError, RuntimeError) as error:
        return replace(empty, error=f"{type(error).__name__}: {error}")
