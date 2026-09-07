"""Immutable skill metadata shared by discovery, prompting, and tools."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class SkillScope(StrEnum):
    """The ownership layer from which a skill was discovered."""

    PROJECT = "project"
    USER = "user"
    SYSTEM = "system"
    PLUGIN = "plugin"


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    """Validated metadata for one materialized ``SKILL.md`` package."""

    name: str
    description: str
    path: Path
    root: Path
    scope: SkillScope
    namespace: str | None = None
    allow_implicit_invocation: bool = True
    short_description: str | None = None
    discovery_path: Path | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}:{self.name}" if self.namespace else self.name


@dataclass(frozen=True, slots=True)
class SkillLoadError:
    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class SkillSnapshot:
    """One deterministic discovery result for a working directory."""

    skills: tuple[SkillMetadata, ...] = ()
    errors: tuple[SkillLoadError, ...] = ()
    disabled_paths: frozenset[Path] = frozenset()

    def is_enabled(self, skill: SkillMetadata) -> bool:
        return skill.path not in self.disabled_paths

    def is_visible(self, skill: SkillMetadata) -> bool:
        return self.is_enabled(skill) and skill.allow_implicit_invocation

    def resolve(self, name: str) -> SkillMetadata | None:
        """Resolve only an unambiguous enabled name or qualified name."""

        normalized = name.strip().removeprefix("$").casefold()
        enabled = [skill for skill in self.skills if self.is_enabled(skill)]
        exact = [skill for skill in enabled if skill.qualified_name.casefold() == normalized]
        if len(exact) == 1:
            return exact[0]
        bare = [skill for skill in enabled if skill.name.casefold() == normalized]
        return bare[0] if len(bare) == 1 else None
