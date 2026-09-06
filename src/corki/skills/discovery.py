"""Codex-style project, user, system, and plugin skill discovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from corki.skills.models import SkillLoadError, SkillMetadata, SkillScope, SkillSnapshot
from corki.skills.parser import parse_skill

_SKIPPED_PARTS = frozenset({".git", ".archive", ".system", "node_modules", ".venv", "__pycache__"})
_SUPPORT_PARTS = frozenset({"references", "scripts", "assets", "templates"})


@dataclass(frozen=True, slots=True)
class SkillRoot:
    path: Path
    scope: SkillScope
    namespace: str | None = None


def discover_skills(roots: tuple[SkillRoot, ...]) -> SkillSnapshot:
    """Scan roots in precedence order and keep the first qualified identity."""

    skills: list[SkillMetadata] = []
    errors: list[SkillLoadError] = []
    seen: set[str] = set()
    for root in roots:
        if not root.path.is_dir():
            continue
        resolved_root = root.path.resolve()
        for path in skill_files(root):
            relative = path.relative_to(root.path)
            if any(part in _SKIPPED_PARTS for part in relative.parts):
                continue
            if any(part in _SUPPORT_PARTS for part in relative.parts[:-1]):
                continue
            try:
                resolved = path.resolve(strict=True)
                # Codex follows user/repository skill directory symlinks so a
                # shared skill checkout can be linked into `.agents/skills`.
                # Bundled system skills stay physically contained.
                if root.scope is SkillScope.SYSTEM:
                    resolved.relative_to(resolved_root)
                skill = parse_skill(
                    resolved,
                    root=resolved_root,
                    scope=root.scope,
                    namespace=root.namespace,
                )
            except (OSError, UnicodeError, ValueError) as exc:
                errors.append(SkillLoadError(path, str(exc)))
                continue
            identity = skill.qualified_name.casefold()
            if identity in seen:
                continue
            seen.add(identity)
            skills.append(skill)
    return SkillSnapshot(tuple(skills), tuple(errors))


def skill_files(root: SkillRoot) -> tuple[Path, ...]:
    """Walk deterministically, following non-system directory links once."""

    found: list[Path] = []
    pending = [root.path]
    visited: set[Path] = set()
    while pending:
        directory = pending.pop()
        try:
            resolved_directory = directory.resolve(strict=True)
        except OSError:
            continue
        if resolved_directory in visited:
            continue
        visited.add(resolved_directory)
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name, reverse=True)
        except OSError:
            continue
        for entry in entries:
            if entry.name == "SKILL.md" and entry.is_file():
                found.append(entry)
                continue
            if entry.name in _SKIPPED_PARTS or entry.name in _SUPPORT_PARTS:
                continue
            try:
                is_directory = entry.is_dir()
            except OSError:
                continue
            if is_directory and not (root.scope is SkillScope.SYSTEM and entry.is_symlink()):
                pending.append(entry)
    return tuple(sorted(found, key=lambda item: str(item)))
