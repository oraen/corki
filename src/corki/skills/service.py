"""Skill installation, cached discovery, selection, and bounded reading."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path

from corki.skills.discovery import SkillRoot, discover_skills, skill_files
from corki.skills.installer import install_bundled_skills
from corki.skills.models import SkillMetadata, SkillScope, SkillSnapshot

MAX_SKILL_CONTENT_BYTES = 128 * 1_024
MAX_CATALOG_CHARS = 48_000
_MENTION = re.compile(r"(?<![\w$])\$([A-Za-z0-9_.:-]+)")


@dataclass(frozen=True, slots=True)
class PluginSkillRoot:
    namespace: str
    path: Path


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
    ) -> None:
        self.home = home
        self.project_root = project_root.resolve()
        self._compatibility_home = compatibility_home
        self._plugin_roots = plugin_roots
        self._bundled_enabled = bundled_enabled
        self._lock = threading.RLock()
        self._cache_key: tuple[tuple[str, int, int], ...] | None = None
        self._snapshot = SkillSnapshot()
        if bundled_enabled:
            install_bundled_skills(home)

    def snapshot(self, cwd: Path, *, force_reload: bool = False) -> SkillSnapshot:
        roots = self._roots(cwd)
        key = _root_signature(roots)
        with self._lock:
            if not force_reload and self._cache_key == key:
                return self._snapshot
            self._snapshot = discover_skills(roots)
            self._cache_key = key
            return self._snapshot

    def explicit_mentions(self, text: str, cwd: Path) -> tuple[SkillMetadata, ...]:
        snapshot = self.snapshot(cwd)
        selected: list[SkillMetadata] = []
        seen: set[Path] = set()
        for match in _MENTION.finditer(text):
            skill = snapshot.resolve(match.group(1))
            if skill is not None and skill.path not in seen:
                selected.append(skill)
                seen.add(skill.path)
        return tuple(selected)

    def read(self, skill: SkillMetadata, relative_file: str = "SKILL.md") -> str:
        """Read one text resource contained by a discovered skill package."""

        skill_dir = skill.path.parent.resolve()
        relative = Path(relative_file)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("skill resource must be a relative path without '..'")
        target = (skill_dir / relative).resolve(strict=True)
        target.relative_to(skill_dir)
        if not target.is_file():
            raise ValueError("skill resource is not a file")
        data = target.read_bytes()
        if len(data) > MAX_SKILL_CONTENT_BYTES:
            raise ValueError("skill resource exceeds 128 KiB")
        return data.decode("utf-8-sig", errors="strict")

    def render_catalog(self, cwd: Path) -> str:
        snapshot = self.snapshot(cwd)
        lines = [
            "## Skills",
            "A skill is a reusable instruction package whose full body is loaded only when needed.",
            "### Available skills",
        ]
        for skill in snapshot.skills:
            lines.append(
                f"- {skill.qualified_name}: {skill.description} "
                f"(file: {skill.path}; scope: {skill.scope.value})"
            )
        rendered = "\n".join(lines)
        if len(rendered) <= MAX_CATALOG_CHARS:
            return rendered
        return (
            rendered[: MAX_CATALOG_CHARS - 80]
            + "\n- ... additional skills omitted by catalog budget"
        )

    def _roots(self, cwd: Path) -> tuple[SkillRoot, ...]:
        resolved_cwd = cwd.resolve()
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
            SkillRoot(value.path, SkillScope.PLUGIN, value.namespace)
            for value in self._plugin_roots
        )
        return tuple(roots)


def _root_signature(roots: tuple[SkillRoot, ...]) -> tuple[tuple[str, int, int], ...]:
    """Fingerprint every entry so editing a non-newest skill invalidates the cache."""

    signature: list[tuple[str, int, int]] = []
    for root in roots:
        if not root.path.exists():
            signature.append((str(root.path), 0, 0))
            continue
        files = skill_files(root)
        if not files:
            try:
                modified = root.path.stat().st_mtime_ns
            except OSError:
                modified = 0
            signature.append((str(root.path), modified, 0))
            continue
        for file in files:
            try:
                stat = file.stat()
            except OSError:
                signature.append((str(file), 0, 0))
            else:
                signature.append((str(file), stat.st_mtime_ns, stat.st_size))
    return tuple(signature)
