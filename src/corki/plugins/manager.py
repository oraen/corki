"""Deterministic local plugin discovery, manifest parsing, and registration."""

from __future__ import annotations

import importlib.util
import inspect
import json
import re
import sys
import tomllib
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

from corki.config.features import MCPServerSettings, parse_mcp_servers
from corki.plugins.api import PluginRegistrar
from corki.plugins.models import LoadedPlugin, PluginManifest
from corki.skills.service import PluginSkillRoot
from corki.tools import ToolRegistry

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_MAX_PLUGINS = 256
_MAX_DESCRIPTION_CHARS = 1_024


class PluginManager:
    """Load trusted plugins once; plugin capability is namespaced by manifest name."""

    def __init__(
        self,
        plugins: tuple[LoadedPlugin, ...],
        modules: tuple[ModuleType, ...],
        warnings: tuple[str, ...] = (),
    ) -> None:
        self.plugins = plugins
        self._modules = modules
        self.warnings = warnings

    @classmethod
    def discover_and_load(
        cls,
        *,
        roots: tuple[Path, ...],
        disabled: frozenset[str],
        registry: ToolRegistry,
    ) -> PluginManager:
        manifests: list[PluginManifest] = []
        warnings: list[str] = []
        seen: set[str] = set()
        for manifest_path in _manifest_paths(roots):
            if len(manifests) >= _MAX_PLUGINS:
                warnings.append("plugin discovery stopped at the 256-package limit")
                break
            try:
                manifest = _parse_manifest(manifest_path)
            except Exception as exc:  # one optional package must not prevent startup
                warnings.append(f"{manifest_path}: {type(exc).__name__}: {exc}")
                continue
            if manifest.name in disabled or manifest.name in seen:
                continue
            seen.add(manifest.name)
            manifests.append(manifest)

        loaded: list[LoadedPlugin] = []
        modules: list[ModuleType] = []
        for manifest in manifests:
            registrar = PluginRegistrar(manifest.name, registry)
            module: ModuleType | None = None
            try:
                if manifest.entrypoint:
                    module, function = _load_entrypoint(manifest)
                    if inspect.iscoroutinefunction(function):
                        raise TypeError("plugin register functions must be synchronous")
                    result = function(registrar)
                    if inspect.isawaitable(result):
                        close = getattr(result, "close", None)
                        if close is not None:
                            with suppress(Exception):
                                close()
                        raise TypeError("plugin register functions must be synchronous")
                    modules.append(module)
                loaded.append(LoadedPlugin(manifest, registrar.registered_tools))
            except BaseException as exc:  # roll back all partially loaded plugin state
                for tool_name in registrar.registered_tools:
                    registry.unregister(tool_name)
                if module is not None:
                    sys.modules.pop(module.__name__, None)
                if isinstance(exc, Exception):
                    warnings.append(f"{manifest.name}: {type(exc).__name__}: {exc}")
                else:
                    raise
        return cls(tuple(loaded), tuple(modules), tuple(warnings))

    @property
    def skill_roots(self) -> tuple[PluginSkillRoot, ...]:
        return tuple(
            PluginSkillRoot(plugin.manifest.name, plugin.manifest.skills_path)
            for plugin in self.plugins
            if plugin.manifest.skills_path is not None
        )

    @property
    def mcp_servers(self) -> tuple[MCPServerSettings, ...]:
        return tuple(server for plugin in self.plugins for server in plugin.manifest.mcp_servers)

    async def aclose(self) -> None:
        errors: list[BaseException] = []
        modules, self._modules = self._modules, ()
        for module in reversed(modules):
            try:
                close = getattr(module, "aclose", None)
                if close is not None:
                    result = close()
                    if inspect.isawaitable(result):
                        await result
            except BaseException as exc:
                errors.append(exc)
            finally:
                sys.modules.pop(module.__name__, None)
        if errors:
            raise errors[0]


def _manifest_paths(roots: tuple[Path, ...]) -> tuple[Path, ...]:
    paths: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        direct = _manifest_in(root)
        if direct is not None:
            paths.append(direct)
            continue
        for child in sorted(root.iterdir(), key=lambda value: value.name):
            path = _manifest_in(child) if child.is_dir() else None
            if path is not None:
                paths.append(path)
    return tuple(paths)


def _parse_manifest(path: Path) -> PluginManifest:
    root = path.parent.parent.resolve()
    value = _read_mapping(path)
    plugin = value.get("plugin", value)
    if not isinstance(plugin, Mapping):
        raise ValueError(f"invalid plugin manifest: {path}")
    name = plugin.get("name")
    if not isinstance(name, str) or _NAME.fullmatch(name) is None:
        raise ValueError(f"invalid plugin name in {path}")
    version = plugin.get("version", "0.0.0")
    description = plugin.get("description", "")
    if not isinstance(version, str) or not isinstance(description, str):
        raise ValueError(f"plugin version and description must be strings: {path}")
    if len(version) > 128 or len(description) > _MAX_DESCRIPTION_CHARS:
        raise ValueError(f"plugin version or description is too long: {path}")
    entrypoint = plugin.get("entrypoint")
    if entrypoint is not None and not isinstance(entrypoint, str):
        raise ValueError(f"plugin entrypoint must be a string: {path}")
    skills_value = plugin.get("skills", "skills")
    if skills_value is not None and not isinstance(skills_value, str):
        raise ValueError(f"plugin skills must be a relative path: {path}")
    skills_path = _contained(root, skills_value) if skills_value else None
    if skills_path is not None and not skills_path.is_dir():
        skills_path = None
    servers = _plugin_mcp_servers(root, name, value, plugin)
    return PluginManifest(
        name=name,
        version=version,
        description=description.strip(),
        root=root,
        entrypoint=entrypoint,
        skills_path=skills_path,
        mcp_servers=tuple(servers),
    )


def _manifest_in(root: Path) -> Path | None:
    """Prefer Corki's extended manifest but accept native Codex packages."""

    candidates = (
        root / ".corki-plugin" / "plugin.toml",
        root / ".codex-plugin" / "plugin.json",
    )
    return next((path for path in candidates if path.is_file()), None)


def _read_mapping(path: Path) -> dict[str, Any]:
    if path.suffix == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
    else:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"plugin manifest must contain an object: {path}")
    return value


def _plugin_mcp_servers(
    root: Path,
    plugin_name: str,
    document: Mapping[str, Any],
    plugin: Mapping[str, Any],
) -> tuple[MCPServerSettings, ...]:
    """Resolve inline or companion-file MCP declarations with containment."""

    declarations: list[object] = []
    mcp_section = document.get("mcp")
    if mcp_section is not None:
        if not isinstance(mcp_section, Mapping):
            raise ValueError("plugin mcp section must be a table")
        declarations.append(mcp_section.get("servers"))

    codex_value = plugin.get("mcpServers")
    if isinstance(codex_value, str):
        declarations.append(_read_mapping(_contained(root, codex_value)).get("mcpServers"))
    elif codex_value is not None:
        declarations.append(codex_value)
    default_mcp = root / ".mcp.json"
    if codex_value is None and default_mcp.is_file():
        declarations.append(_read_mapping(default_mcp).get("mcpServers"))

    servers = []
    seen: set[str] = set()
    for declaration in declarations:
        for server in parse_mcp_servers(declaration):
            if server.name in seen:
                continue
            seen.add(server.name)
            servers.append(
                replace(
                    server,
                    name=f"{plugin_name}__{server.name}",
                    cwd=server.cwd or root,
                )
            )
    return tuple(servers)


def _contained(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("plugin paths must stay inside the plugin directory")
    resolved = (root / path).resolve()
    resolved.relative_to(root)
    return resolved


def _load_entrypoint(manifest: PluginManifest):
    assert manifest.entrypoint is not None
    module_path, separator, function_name = manifest.entrypoint.partition(":")
    if not separator or not function_name:
        raise ValueError(f"plugin entrypoint must use file.py:function: {manifest.name}")
    path = _contained(manifest.root, module_path)
    if not path.is_file() or path.suffix != ".py":
        raise ValueError(f"plugin entrypoint does not exist: {path}")
    # Two embedded Corki runtimes may load the same plugin concurrently. A
    # unique module key prevents one manager's close from unloading the other.
    module_name = f"corki_plugin_{manifest.name}_{uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load plugin module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    sys.path.insert(0, str(manifest.root))
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    finally:
        sys.path.remove(str(manifest.root))
    function = getattr(module, function_name, None)
    if not callable(function):
        sys.modules.pop(module_name, None)
        raise ValueError(f"plugin entrypoint is not callable: {manifest.entrypoint}")
    return module, function
