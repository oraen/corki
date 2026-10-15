"""Deterministic local plugin discovery, manifest parsing, and registration."""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import re
import sys
import tomllib
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import uuid4

from corki.config.features import MCPServerSettings
from corki.config.mcp_requirements import MCPServerSource
from corki.mcp.catalog import MCPCatalogSource, MCPRegistration
from corki.plugins.agent_manifest import parse_agent_manifest
from corki.plugins.api import PluginRegistrar
from corki.plugins.hooks import load_plugin_hooks
from corki.plugins.installed_manifest import installed_manifest
from corki.plugins.manifest_path import ManifestSource, find_manifest
from corki.plugins.mcp import load_plugin_mcp
from corki.plugins.models import LoadedPlugin, PluginManifest
from corki.plugins.presentation import interface_display_name
from corki.plugins.store import PluginStoreSource
from corki.skills.models import SkillDiscoveryMode
from corki.skills.service import PluginSkillRoot
from corki.tools import ToolRegistry, ToolSource

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_MAX_PLUGINS = 256
_MAX_DESCRIPTION_CHARS = 1_024


class PluginManager:
    """Own published plugin contributions and session-lived Python code generations."""

    def __init__(
        self,
        plugins: tuple[LoadedPlugin, ...],
        modules: tuple[ModuleType, ...],
        warnings: tuple[str, ...] = (),
    ) -> None:
        self.plugins = plugins
        self._modules = modules
        self.warnings = warnings
        self._components = {}
        self._registry = None
        self._owner = None
        self._closed = False
        self._close_task = None
        self._generation = 0

    @classmethod
    def discover_and_load(
        cls,
        *,
        roots: tuple[Path | PluginStoreSource, ...],
        disabled: frozenset[str],
        registry: ToolRegistry,
        on_created=None,
    ) -> PluginManager:
        manager = cls((), ())
        manager._registry = registry
        manager._owner = registry.create_owner(source=ToolSource.EXTENSION)
        if on_created is not None:
            on_created(manager)
        manager.prepare_reload(*discover_manifests(roots, disabled)).publish()
        return manager

    def prepare_reload(self, manifests, warnings):
        """Register only new code into isolated registries, then stage one publication."""
        if self._closed:
            raise RuntimeError("plugin manager is closed")
        loaded, modules = [], []
        components = dict(self._components)
        warnings = list(warnings)
        for manifest, key, code in manifests:
            tools = components.get(key)
            if tools is None:
                registrar = PluginRegistrar(manifest.name, ToolRegistry())
                module = None
                try:
                    if manifest.entrypoint:
                        module, function = _load_entrypoint(manifest, modules=modules, code=code)
                        if inspect.iscoroutinefunction(function):
                            raise TypeError("plugin register functions must be synchronous")
                        result = function(registrar)
                        if inspect.isawaitable(result):
                            close = getattr(result, "close", None)
                            if close is not None:
                                with suppress(Exception):
                                    close()
                            raise TypeError("plugin register functions must be synchronous")
                    tools = registrar.tools
                    components[key] = tools
                except BaseException as exc:
                    registrar.rollback()
                    if module is not None:
                        sys.modules.pop(module.__name__, None)
                    if isinstance(exc, Exception):
                        warnings.append(f"{manifest.name}: {type(exc).__name__}: {exc}")
                        continue
                    # No PluginReload is returned for the caller to abort. Keep
                    # every newly loaded module owned until manager shutdown.
                    self._modules = (*self._modules, *modules)
                    for pending in modules:
                        sys.modules.pop(pending.__name__, None)
                    raise
            loaded.append((LoadedPlugin(manifest, tuple(tool.spec.name for tool in tools)), tools))
        view = PluginManager(tuple(plugin for plugin, _ in loaded), (), tuple(warnings))
        tools = tuple(tool for _, group in loaded for tool in group)
        try:
            publish_tools = self._registry.prepare_external(self._owner, tools)
        except BaseException:
            # Runtime can close these staged modules when preparation is rejected.
            self._modules = (*self._modules, *modules)
            raise
        return PluginReload(self, view, tuple(modules), components, publish_tools)

    @property
    def skill_roots(self) -> tuple[PluginSkillRoot, ...]:
        return tuple(
            PluginSkillRoot(
                plugin.manifest.name,
                path,
                plugin.manifest.identity,
                plugin.manifest.root,
                SkillDiscoveryMode.DIRECT_CHILDREN
                if plugin.manifest.agent_plugin
                else SkillDiscoveryMode.RECURSIVE,
            )
            for plugin in self.plugins
            if plugin.manifest.enabled and plugin.manifest.error is None
            for path in plugin.manifest.skill_paths
        )

    @property
    def mcp_servers(self) -> tuple[MCPServerSettings, ...]:
        return tuple(server for plugin in self.plugins for server in plugin.manifest.mcp_servers)

    @property
    def mcp_registrations(self) -> tuple[MCPRegistration, ...]:
        """Resolve native logical names with explicit package order and exact host roots.

        mcp_servers remains the legacy manifest projection; Runtime consumes these
        declarations, so package prefixes cannot hide logical server collisions.
        """
        registrations = []
        for order, plugin in enumerate(sorted(self.plugins, key=lambda p: p.manifest.identity)):
            source = MCPCatalogSource(
                "plugin",
                plugin.manifest.identity,
                order,
                plugin.manifest.root,
                agent_plugin=plugin.manifest.agent_plugin,
                display_name=plugin.manifest.display_name,
            )
            raw_names = dict(plugin.manifest.mcp_raw_names)
            for server in plugin.manifest.mcp_servers:
                raw = raw_names.get(server.name, server.name)
                registrations.append(MCPRegistration(replace(server, name=raw), source))
        return tuple(registrations)

    @property
    def mcp_server_sources(self) -> dict[str, MCPServerSource]:
        """Keep raw plugin declaration names separate from Corki's callable routes."""
        result = {}
        for plugin in self.plugins:
            raw_names = dict(plugin.manifest.mcp_raw_names)
            for server in plugin.manifest.mcp_servers:
                result[server.name] = MCPServerSource(
                    raw_names.get(server.name, server.name),
                    plugin.manifest.identity,
                )
        return result

    async def aclose(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._close_modules(), name="plugin-close")
        interrupted = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                interrupted = True
            except BaseException:
                break
        try:
            self._close_task.result()
        finally:
            if interrupted:
                raise asyncio.CancelledError

    async def _close_modules(self) -> None:
        self._closed = True
        self._components.clear()
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


class PluginReload:
    def __init__(self, manager, view, modules, components, publish_tools):
        self.manager, self.view = manager, view
        self.modules, self.components = modules, components
        self._publish_tools = publish_tools
        self._generation = manager._generation
        self._finished = False
        self._abort_manager = None

    def validate(self):
        if self._finished or self.manager._closed or self.manager._generation != self._generation:
            raise RuntimeError("plugin candidate no longer owns publication")

    def publish(self):
        self.validate()
        self._publish_tools()
        self.manager.plugins, self.manager.warnings = self.view.plugins, self.view.warnings
        self.manager._modules = (*self.manager._modules, *self.modules)
        self.manager._components = self.components
        self.modules = ()
        self._finished = True
        self.manager._generation += 1

    async def abort(self):
        self._finished = True
        if self._abort_manager is None:
            modules, self.modules = self.modules, ()
            self._abort_manager = PluginManager((), modules)
        await self._abort_manager.aclose()


def discover_manifests(roots, disabled):
    """Discover metadata/data roots; disabled packages never execute registration code."""
    manifests, warnings, seen = [], [], set()
    for source in _manifest_paths(tuple(root for root in roots if isinstance(root, Path))):
        if len(manifests) >= _MAX_PLUGINS:
            warnings.append("plugin discovery stopped at the 256-package limit")
            break
        try:
            manifest = _parse_manifest(source)
            if manifest.identity in disabled:
                seen.add(manifest.identity)
                continue
            if manifest.identity in seen:
                continue
            digest = code = None
            if manifest.entrypoint:
                module_path = manifest.entrypoint.partition(":")[0]
                code = _contained(manifest.root, module_path).read_bytes()
                digest = sha256(code).digest()
            key = (manifest.identity, manifest.root, manifest.entrypoint, digest)
        except Exception as exc:
            warnings.append(f"{source.path}: {type(exc).__name__}: {exc}")
            continue
        seen.add(manifest.identity)
        manifests.append((manifest, key, code))
        warnings.extend(f"{manifest.name}: {warning}" for warning in manifest.warnings)
        warnings.extend(f"{manifest.name} MCP: {warning}" for warning in manifest.mcp_warnings)
    for store in (root for root in roots if isinstance(root, PluginStoreSource)):
        for installation in store.installations():
            if installation.identity in seen:
                continue
            if len(manifests) >= _MAX_PLUGINS:
                warnings.append("plugin discovery stopped at the 256-package limit")
                break
            if installation.identity in disabled:
                installation = replace(installation, enabled=False, error=None)
            manifest = installed_manifest(installation, store.home)
            seen.add(manifest.identity)
            key = (manifest.identity, manifest.root, None, None)
            manifests.append((manifest, key, None))
            if manifest.error is not None:
                warnings.append(f"{manifest.identity}: {manifest.error}")
            warnings.extend(f"{manifest.identity}: {warning}" for warning in manifest.warnings)
            warnings.extend(
                f"{manifest.identity} MCP: {warning}" for warning in manifest.mcp_warnings
            )
    return tuple(manifests), tuple(warnings)


def _manifest_paths(roots: tuple[Path, ...]) -> tuple[ManifestSource, ...]:
    paths: list[ManifestSource] = []
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


def _parse_manifest(source: ManifestSource) -> PluginManifest:
    if source.agent_plugin:
        return parse_agent_manifest(source.path)
    path = source.path
    root = source.root.resolve()
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
    mcp = load_plugin_mcp(root, value, plugin)
    hook_sources, hook_warnings = load_plugin_hooks(root, plugin.get("hooks"))
    return PluginManifest(
        name=name,
        version=version,
        description=description.strip(),
        root=root,
        entrypoint=entrypoint,
        skills_path=skills_path,
        mcp_servers=tuple(replace(server, name=f"{name}__{server.name}") for server in mcp.servers),
        mcp_raw_names=tuple((f"{name}__{server.name}", server.name) for server in mcp.servers),
        mcp_warnings=mcp.warnings,
        interface_display_name=interface_display_name(plugin),
        hook_sources=hook_sources,
        warnings=hook_warnings,
    )


def _manifest_in(root: Path) -> ManifestSource | None:
    """Select the trusted source format before interpreting package capabilities."""
    return find_manifest(root)


def _read_mapping(path: Path) -> dict[str, Any]:
    if path.suffix == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
    else:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"plugin manifest must contain an object: {path}")
    return value


def _contained(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("plugin paths must stay inside the plugin directory")
    resolved = (root / path).resolve()
    resolved.relative_to(root)
    return resolved


def _load_entrypoint(
    manifest: PluginManifest, *, modules: list[ModuleType], code: bytes | None = None
):
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
    # Module execution can establish resources and then fail before returning a
    # register function. The candidate must already own any available close hook.
    modules.append(module)
    sys.modules[module_name] = module
    sys.path.insert(0, str(manifest.root))
    try:
        # Execute the exact discovery snapshot. importlib's timestamp pyc cache
        # can otherwise reuse stale same-size code after a fast config reload.
        exec(
            compile(code if code is not None else path.read_bytes(), str(path), "exec"),
            module.__dict__,
        )
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
