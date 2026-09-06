"""Strict, bounded parser for the Agent Skills ``SKILL.md`` frontmatter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from corki.skills.models import SkillMetadata, SkillScope

MAX_SKILL_NAME_CHARS = 64
MAX_SKILL_DESCRIPTION_CHARS = 1_024
MAX_FRONTMATTER_BYTES = 16 * 1_024


class SkillParseError(ValueError):
    pass


def parse_skill(
    path: Path,
    *,
    root: Path,
    scope: SkillScope,
    namespace: str | None = None,
) -> SkillMetadata:
    """Read and validate metadata without loading the full instruction body."""

    with path.open("rb") as handle:
        prefix = handle.read(MAX_FRONTMATTER_BYTES + 1)
    text = prefix.decode("utf-8-sig", errors="strict")
    if not text.startswith("---"):
        raise SkillParseError("missing YAML frontmatter")
    end = text.find("\n---", 3)
    if end < 0:
        detail = (
            "frontmatter exceeds 16 KiB"
            if len(prefix) > MAX_FRONTMATTER_BYTES
            else "unterminated YAML frontmatter"
        )
        raise SkillParseError(detail)
    try:
        value: Any = yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError as exc:
        raise SkillParseError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(value, dict):
        raise SkillParseError("frontmatter must be a mapping")
    name = value.get("name")
    description = value.get("description")
    if not isinstance(name, str) or not name.strip():
        raise SkillParseError("missing non-empty name")
    if not isinstance(description, str) or not description.strip():
        raise SkillParseError("missing non-empty description")
    name = name.strip()
    description = " ".join(description.split())
    if len(name) > MAX_SKILL_NAME_CHARS:
        raise SkillParseError("name exceeds 64 characters")
    if len(description) > MAX_SKILL_DESCRIPTION_CHARS:
        raise SkillParseError("description exceeds 1024 characters")
    return SkillMetadata(name, description, path.resolve(), root.resolve(), scope, namespace)
