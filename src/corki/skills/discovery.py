"""Codex-style project, user, system, and plugin skill discovery."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path

from corki.skills.io import check_skill_io
from corki.skills.models import (
    SkillDiscoveryMode,
    SkillLoadError,
    SkillMetadata,
    SkillScope,
    SkillSnapshot,
)
from corki.skills.parser import parse_skill
from corki.skills.walk import SkillWalk, walk_skill_files

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SkillRoot:
    path: Path
    scope: SkillScope
    namespace: str | None = None
    plugin_id: str | None = None
    plugin_root: Path | None = None
    discovery_mode: SkillDiscoveryMode = SkillDiscoveryMode.RECURSIVE


def discover_skills(
    roots: tuple[SkillRoot, ...], *, inventories: tuple[SkillWalk, ...] | None = None
) -> SkillSnapshot:
    """Retain distinct source paths; names are selectors, not package identities."""

    skills: list[SkillMetadata] = []
    errors: list[SkillLoadError] = []
    seen: set[Path] = set()
    if inventories is None:
        inventories = tuple(scan_skill_root(root) for root in roots)
    for root, inventory in zip(roots, inventories, strict=True):
        check_skill_io()
        for error in inventory.errors:
            _LOG.warning("Failed to scan skill path %s: %s", error.path, error.message)
        if inventory.truncated:
            _LOG.warning("Skills scan reached its traversal limit (root: %s)", root.path)
        if not root.path.is_dir():
            continue
        resolved_root = root.path.resolve()
        for path in inventory.files:
            check_skill_io()
            try:
                resolved = path.resolve(strict=True)
                if root.discovery_mode is SkillDiscoveryMode.DIRECT_CHILDREN and (
                    root.plugin_root is None
                    or not resolved.is_relative_to(root.plugin_root.resolve())
                    or not resolved.is_file()
                ):
                    _LOG.warning("Skipping Agent Plugin skill outside its plugin root: %s", path)
                    continue
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
                check_skill_io()
                skill = replace(
                    skill,
                    discovery_path=path.absolute(),
                    plugin_id=root.plugin_id,
                    plugin_root=root.plugin_root.resolve() if root.plugin_root else None,
                )
            except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
                errors.append(SkillLoadError(path, str(exc)))
                continue
            skills.append(skill)

    # A plugin's authored skills supersede migrated commands with the same name,
    # independent of which root was scanned first. Other same-name paths survive.
    def migrated(skill):
        check_skill_io()
        return skill.plugin_root is not None and skill.path.is_relative_to(
            (skill.plugin_root / ".codex-plugin/migrated-command-skills").resolve()
        )

    authored = {(s.plugin_id, s.qualified_name) for s in skills if s.plugin_id and not migrated(s)}
    skills = [
        s for s in skills if not (migrated(s) and (s.plugin_id, s.qualified_name) in authored)
    ]
    distinct = []
    for skill in skills:
        if skill.path not in seen:
            seen.add(skill.path)
            distinct.append(skill)
    skills = distinct
    ranks = {SkillScope.PROJECT: 0, SkillScope.USER: 1, SkillScope.PLUGIN: 1, SkillScope.SYSTEM: 2}
    skills.sort(key=lambda s: (ranks[s.scope], s.qualified_name, str(s.path)))
    return SkillSnapshot(tuple(skills), tuple(errors))


def skill_files(root: SkillRoot) -> tuple[Path, ...]:
    """Compatibility projection; Runtime also retains scan diagnostics."""
    return scan_skill_root(root).files


def scan_skill_root(root: SkillRoot) -> SkillWalk:
    return walk_skill_files(
        root.path,
        mode=root.discovery_mode,
        follow_directory_links=root.scope is not SkillScope.SYSTEM,
    )
