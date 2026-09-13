"""Skill installation, cached discovery, selection, and bounded reading."""

from __future__ import annotations

import threading
from copy import copy
from dataclasses import dataclass, replace
from pathlib import Path

from corki.config.skills import SkillRule, disabled_skill_paths, skill_rules_from_layers
from corki.skills.catalog import MetadataBudget, render_catalog
from corki.skills.discovery import SkillRoot, discover_skills, scan_skill_root
from corki.skills.installer import install_bundled_skills
from corki.skills.io import check_skill_io, publish_skill_io
from corki.skills.mentions import select
from corki.skills.models import SkillDiscoveryMode, SkillMetadata, SkillScope, SkillSnapshot
from corki.skills.policy import metadata_path
from corki.skills.walk import SkillWalk

MAX_SKILL_CONTENT_BYTES = 128 * 1_024


@dataclass(frozen=True, slots=True)
class PluginSkillRoot:
    namespace: str
    path: Path
    plugin_id: str | None = None
    plugin_root: Path | None = None
    discovery_mode: SkillDiscoveryMode = SkillDiscoveryMode.RECURSIVE


class SkillService:
    """Own the immutable skill snapshot visible to one Corki process."""

    def __init__(
        self,
        *,
        home: Path,
        project_root: Path,
        compatibility_home: Path | None = None,
        plugin_roots: tuple[PluginSkillRoot, ...] = (),
        bundled_enabled: bool = True,
        context_window_tokens: int | None = None,
        max_context_tokens: int | None = None,
        rules: tuple[SkillRule, ...] = (),
        configured_project_roots: tuple[Path, ...] = (),
        discovery_cwd: Path | None = None,
    ) -> None:
        self.home = home
        self.project_root = project_root.resolve()
        self._compatibility_home = compatibility_home
        self._plugin_roots = plugin_roots
        self._bundled_enabled = bundled_enabled
        self._catalog_budget = MetadataBudget.resolve(context_window_tokens, max_context_tokens)
        self._max_context_tokens = max_context_tokens
        self._rules = rules
        self._configured_project_roots = configured_project_roots
        self._discovery_cwd = discovery_cwd
        self._lock = threading.RLock()
        self._cache_key: tuple[tuple[str, int, int], ...] | None = None
        self._snapshot = SkillSnapshot()
        if bundled_enabled:
            install_bundled_skills(home)

    def with_context_window(self, tokens: int) -> SkillService:
        """Share discovery inputs/cache while retaining an independent metadata budget."""
        service = copy(self)
        service._catalog_budget = MetadataBudget.resolve(tokens, self._max_context_tokens)
        return service

    def with_configuration(self, configuration) -> SkillService:
        """Prepare new layer-backed rules without changing an admitted Turn's view."""
        service = copy(self)
        service._rules = (
            *skill_rules_from_layers(configuration),
            *(rule for rule in self._rules if rule.source == "host"),
        )
        service._lock = threading.RLock()
        service._cache_key = None
        service._snapshot = SkillSnapshot()
        return service

    def with_plugin_roots(self, roots: tuple[PluginSkillRoot, ...]) -> SkillService:
        service = copy(self)
        service._plugin_roots = tuple(roots)
        service._lock = threading.RLock()
        service._cache_key = None
        service._snapshot = SkillSnapshot()
        return service

    def snapshot(self, cwd: Path, *, force_reload: bool = False) -> SkillSnapshot:
        check_skill_io()
        roots = self._roots(cwd)
        inventories = tuple(scan_skill_root(root) for root in roots)
        key = _root_signature(roots, inventories)
        with self._lock:
            check_skill_io()
            if not force_reload and self._cache_key == key:
                return self._snapshot
            snapshot = discover_skills(roots, inventories=inventories)
            snapshot = replace(
                snapshot,
                disabled_paths=disabled_skill_paths(
                    self._rules,
                    ((skill.qualified_name, skill.path) for skill in snapshot.skills),
                ),
            )

            def publish():
                self._snapshot = snapshot
                self._cache_key = key
                return snapshot

            return publish_skill_io(publish)

    def explicit_mentions(self, text: str, cwd: Path, *, mentions=()) -> tuple[SkillMetadata, ...]:
        return select(text, self.snapshot(cwd), mentions=mentions)

    def read(self, skill: SkillMetadata, relative_file: str = "SKILL.md") -> str:
        """Read one text resource contained by a discovered skill package."""

        check_skill_io()
        skill_dir = skill.path.parent.resolve()
        relative = Path(relative_file)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("skill resource must be a relative path without '..'")
        target = (skill_dir / relative).resolve(strict=True)
        target.relative_to(skill_dir)
        if not target.is_file():
            raise ValueError("skill resource is not a file")
        check_skill_io()
        data = target.read_bytes()
        check_skill_io()
        if len(data) > MAX_SKILL_CONTENT_BYTES:
            raise ValueError("skill resource exceeds 128 KiB")
        return data.decode("utf-8-sig", errors="strict")

    def render_catalog(self, cwd: Path) -> str:
        return self.catalog(cwd).body

    def catalog(self, cwd: Path):
        snapshot = self.snapshot(cwd)
        return render_catalog(
            tuple(skill for skill in snapshot.skills if snapshot.is_visible(skill)),
            self._catalog_budget,
        )

    def _roots(self, cwd: Path) -> tuple[SkillRoot, ...]:
        resolved_cwd = (self._discovery_cwd or cwd).resolve()
        try:
            resolved_cwd.relative_to(self.project_root)
        except ValueError:
            project_dirs = (resolved_cwd,)
        else:
            project_dirs = tuple(
                path
                for path in resolved_cwd.parents
                if path == self.project_root or self.project_root in path.parents
            )
            project_dirs = (resolved_cwd, *project_dirs)
        roots = [
            *(SkillRoot(path, SkillScope.PROJECT) for path in self._configured_project_roots),
            *(
                SkillRoot(path / directory / "skills", SkillScope.PROJECT)
                for path in project_dirs
                for directory in (".corki", ".codex", ".agents")
            ),
            SkillRoot(self.home / "skills", SkillScope.USER),
        ]
        if self._compatibility_home is not None:
            roots.extend(
                (
                    SkillRoot(
                        self._compatibility_home / ".agents" / "skills",
                        SkillScope.USER,
                    ),
                    SkillRoot(
                        self._compatibility_home / ".codex" / "skills",
                        SkillScope.USER,
                    ),
                )
            )
        if self._bundled_enabled:
            roots.append(SkillRoot(self.home / "skills" / ".system", SkillScope.SYSTEM))
        roots.extend(
            SkillRoot(
                value.path,
                SkillScope.USER,
                value.namespace,
                value.plugin_id,
                value.plugin_root,
                value.discovery_mode,
            )
            for value in self._plugin_roots
        )
        return tuple(roots)

    def configured_project_roots(self, cwd: Path) -> tuple[Path, ...]:
        """Retain configuration-folder roots, not cwd-discovered .agents roots."""
        return tuple(
            dict.fromkeys(
                root.path
                for root in self._roots(cwd)
                if root.scope is SkillScope.PROJECT
                and root.path.parent.name in {".corki", ".codex"}
            )
        )


def _root_signature(
    roots: tuple[SkillRoot, ...], inventories: tuple[SkillWalk, ...]
) -> tuple[tuple[str, int, int], ...]:
    """Fingerprint every entry so editing a non-newest skill invalidates the cache."""

    signature: list[tuple[str, int, int]] = []
    for root, inventory in zip(roots, inventories, strict=True):
        check_skill_io()
        signature.append((repr(root), 0, 0))
        signature.append((repr(inventory.errors), int(inventory.truncated), 0))
        if not root.path.exists():
            signature.append((str(root.path), 0, 0))
            continue
        files = inventory.files
        if not files:
            try:
                modified = root.path.stat().st_mtime_ns
            except OSError:
                modified = 0
            signature.append((str(root.path), modified, 0))
            continue
        for file in (
            path for skill_file in files for path in (skill_file, metadata_path(skill_file))
        ):
            check_skill_io()
            try:
                stat = file.stat()
                canonical = file.resolve(strict=True)
            except OSError:
                signature.append((str(file), 0, 0))
            else:
                signature.append(
                    (repr((str(file), str(canonical))), stat.st_mtime_ns, stat.st_size)
                )
    return tuple(signature)
